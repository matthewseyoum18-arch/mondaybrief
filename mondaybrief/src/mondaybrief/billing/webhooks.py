"""Stripe webhook handler (FastAPI router).

Mount with::

    from mondaybrief.billing.webhooks import router as stripe_router
    app.include_router(stripe_router)

The endpoint is ``POST /webhooks/stripe``. Stripe must be pointed at it
with the signing secret exposed as ``STRIPE_WEBHOOK_SECRET``. We verify
the signature before touching the DB; any failure returns 400 so Stripe
retries.

Handled events:

* ``checkout.session.completed`` — first paid signup. Upserts the
  ``clients`` row (in case onboarding never finished server-side) and
  inserts the ``subscriptions`` row.
* ``customer.subscription.updated`` — keeps ``status`` and
  ``current_period_end`` fresh. Also flips ``clients.active`` based on
  whether the subscription is in a paying state.
* ``customer.subscription.deleted`` — hard-suspend: ``clients.active``
  goes to ``false`` and the brief stops shipping.
* ``invoice.payment_failed`` — fires one dunning email per attempt.
  Stripe Smart Retries owns the retry cadence (3 tries, ~3/5/7 days);
  we just notify. ``email_events`` gets a ``dunning_sent`` row for the
  audit trail.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import stripe
from fastapi import APIRouter, Header, HTTPException, Request

from ..db import connect
from .dunning import send_dunning_email
from .stripe_client import _init_stripe

router = APIRouter(prefix="/webhooks", tags=["billing"])


def _webhook_secret() -> str:
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not set.")
    return secret


def _ts_to_dt(ts: int | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def _active_states() -> set[str]:
    """Stripe statuses we treat as 'paying'."""
    return {"active", "trialing"}


# --------------------------------------------------------------------- #
# Event handlers — pure functions so they're unit-testable without HTTP.
# --------------------------------------------------------------------- #


def _handle_checkout_completed(event: dict[str, Any]) -> None:
    session = event["data"]["object"]
    client_id = (
        session.get("client_reference_id")
        or (session.get("metadata") or {}).get("client_id")
    )
    if not client_id:
        # Nothing we can do — Checkout was created outside our flow.
        return

    customer_id = session.get("customer")
    subscription_id = session.get("subscription")
    if not subscription_id:
        return

    # Pull the full Subscription so we have status + period end.
    _init_stripe()
    sub = stripe.Subscription.retrieve(subscription_id)

    with connect() as conn:
        # Upsert clients row. The onboarding flow normally inserts it
        # first, but we defensively ensure it exists so a webhook race
        # can't lose us the customer.
        conn.execute(
            """
            UPDATE clients
               SET stripe_customer_id = COALESCE(stripe_customer_id, %s),
                   active = true
             WHERE id = %s
            """,
            (customer_id, client_id),
        )

        # Insert / refresh subscriptions row keyed on stripe_subscription_id.
        conn.execute(
            """
            INSERT INTO subscriptions
                (client_id, stripe_subscription_id, status, current_period_end, updated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (stripe_subscription_id) DO UPDATE
               SET status = EXCLUDED.status,
                   current_period_end = EXCLUDED.current_period_end,
                   updated_at = now()
            """,
            (
                client_id,
                subscription_id,
                sub["status"],
                _ts_to_dt(sub.get("current_period_end")),
            ),
        )


def _handle_subscription_updated(event: dict[str, Any]) -> None:
    sub = event["data"]["object"]
    subscription_id = sub["id"]
    status = sub["status"]
    period_end = _ts_to_dt(sub.get("current_period_end"))
    client_id = (sub.get("metadata") or {}).get("client_id")

    with connect() as conn:
        conn.execute(
            """
            UPDATE subscriptions
               SET status = %s,
                   current_period_end = %s,
                   updated_at = now()
             WHERE stripe_subscription_id = %s
            """,
            (status, period_end, subscription_id),
        )

        if client_id:
            active = status in _active_states()
            conn.execute(
                "UPDATE clients SET active = %s WHERE id = %s",
                (active, client_id),
            )


def _handle_subscription_deleted(event: dict[str, Any]) -> None:
    sub = event["data"]["object"]
    subscription_id = sub["id"]
    client_id = (sub.get("metadata") or {}).get("client_id")

    with connect() as conn:
        conn.execute(
            """
            UPDATE subscriptions
               SET status = 'canceled',
                   updated_at = now()
             WHERE stripe_subscription_id = %s
            """,
            (subscription_id,),
        )
        if client_id:
            conn.execute(
                "UPDATE clients SET active = false WHERE id = %s",
                (client_id,),
            )


def _handle_invoice_payment_failed(event: dict[str, Any]) -> None:
    invoice = event["data"]["object"]
    subscription_id = invoice.get("subscription")
    # Stripe Smart Retries uses ``attempt_count`` (1-indexed).
    attempt = int(invoice.get("attempt_count") or 1)
    next_attempt = invoice.get("next_payment_attempt")  # null → no more retries

    if not subscription_id:
        return

    with connect() as conn:
        row = conn.execute(
            "SELECT client_id FROM subscriptions WHERE stripe_subscription_id = %s",
            (subscription_id,),
        ).fetchone()
        if row is None:
            return
        client_id = row[0]

        # On final failure (Stripe gave up), hard-suspend the brief.
        if next_attempt is None:
            conn.execute(
                "UPDATE clients SET active = false WHERE id = %s",
                (str(client_id),),
            )
            conn.execute(
                """
                UPDATE subscriptions
                   SET status = 'unpaid', updated_at = now()
                 WHERE stripe_subscription_id = %s
                """,
                (subscription_id,),
            )

    # Notify the cleaner with a retry CTA. One email per failed attempt;
    # the schedule (immediate / +3d / +7d) is Stripe Smart Retries, so
    # we don't sleep here.
    send_dunning_email(client_id=str(client_id), attempt_number=attempt)


# --------------------------------------------------------------------- #
# HTTP entrypoint
# --------------------------------------------------------------------- #


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(default="", alias="Stripe-Signature"),
) -> dict[str, str]:
    """Verify + dispatch one Stripe event.

    Returns ``{"received": "ok"}`` on success. Raises 400 on bad
    signature so Stripe retries with exponential backoff.
    """
    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=stripe_signature,
            secret=_webhook_secret(),
        )
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid signature: {exc}")

    event_type = event["type"]

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(event)
    elif event_type == "customer.subscription.updated":
        _handle_subscription_updated(event)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(event)
    elif event_type == "invoice.payment_failed":
        _handle_invoice_payment_failed(event)
    # Any other event type is silently acknowledged so Stripe stops
    # retrying. Add new branches above as needed.

    return {"received": "ok"}
