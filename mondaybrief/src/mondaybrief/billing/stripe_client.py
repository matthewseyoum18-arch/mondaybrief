"""Thin wrapper around the Stripe SDK.

Three callables the rest of the app needs:

* :func:`create_or_get_customer` — idempotent lookup-by-metadata so we
  never double-create a Customer for the same ``clients.id`` UUID.
* :func:`create_checkout_session` — builds a $149/mo subscription
  Checkout Session keyed to ``STRIPE_PRICE_ID_MONTHLY`` and tagged with
  ``client_reference_id`` so the webhook can re-link without DB lookup.
* :func:`get_subscription_status` — reads the locally mirrored
  ``subscriptions`` row (we never poll Stripe from the hot path).
"""
from __future__ import annotations

import os
from typing import Any
from uuid import UUID

import stripe

from ..db import connect


def _init_stripe() -> None:
    """Configure the SDK from env. Called lazily on first use."""
    api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not api_key:
        raise RuntimeError(
            "STRIPE_SECRET_KEY is not set — Stripe billing cannot initialise."
        )
    stripe.api_key = api_key


def _price_id() -> str:
    price = os.environ.get("STRIPE_PRICE_ID_MONTHLY", "")
    if not price:
        raise RuntimeError(
            "STRIPE_PRICE_ID_MONTHLY is not set — cannot create Checkout session."
        )
    return price


def create_or_get_customer(client_id: UUID, email: str) -> stripe.Customer:
    """Return a Stripe Customer for the given MondayBrief client UUID.

    Idempotent: we tag every Customer with ``metadata.client_id`` on
    create, then list-by-metadata on lookup. This survives both DB
    rollbacks and re-runs of the onboarding step.
    """
    _init_stripe()
    client_id_str = str(client_id)

    # Search-by-metadata. ``stripe.Customer.search`` uses the Search API
    # which is eventually consistent — fine for an onboarding step that
    # already serialises behind a user click.
    existing = stripe.Customer.search(
        query=f'metadata["client_id"]:"{client_id_str}"',
        limit=1,
    )
    if existing.data:
        return existing.data[0]

    customer = stripe.Customer.create(
        email=email,
        metadata={"client_id": client_id_str},
    )

    # Mirror back onto clients.stripe_customer_id so we don't pay the
    # Search API tax on every webhook.
    with connect() as conn:
        conn.execute(
            "UPDATE clients SET stripe_customer_id = %s WHERE id = %s",
            (customer.id, client_id_str),
        )

    return customer


def create_checkout_session(
    client_id: UUID,
    client_email: str,
    success_url: str,
    cancel_url: str,
) -> stripe.checkout.Session:
    """Create a Stripe Checkout Session for the $149/mo subscription.

    ``client_id`` flows through three channels so the webhook handler
    can always recover it:

    * ``client_reference_id`` — top-level field, surfaced on
      ``checkout.session.completed``.
    * ``metadata.client_id`` — survives even if Stripe ever drops
      ``client_reference_id`` from a downstream event.
    * ``subscription_data.metadata.client_id`` — copied onto the
      created Subscription so subscription.* events also carry it.
    """
    _init_stripe()
    customer = create_or_get_customer(client_id, client_email)

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer.id,
        client_reference_id=str(client_id),
        line_items=[{"price": _price_id(), "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"client_id": str(client_id)},
        subscription_data={
            "metadata": {"client_id": str(client_id)},
        },
        allow_promotion_codes=True,
    )
    return session


def get_subscription_status(client_id: UUID) -> dict[str, Any]:
    """Read the locally mirrored subscription row for a client.

    Returns ``{"status": "none"}`` if no subscription has ever been
    written. Callers gate brief delivery on
    ``status in {"active", "trialing"}``.
    """
    with connect() as conn:
        row = conn.execute(
            """
            SELECT stripe_subscription_id, status, current_period_end, updated_at
              FROM subscriptions
             WHERE client_id = %s
             ORDER BY updated_at DESC
             LIMIT 1
            """,
            (str(client_id),),
        ).fetchone()

    if row is None:
        return {"status": "none"}

    return {
        "stripe_subscription_id": row[0],
        "status": row[1],
        "current_period_end": row[2],
        "updated_at": row[3],
    }
