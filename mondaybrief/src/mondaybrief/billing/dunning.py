"""Dunning notification emails for failed Stripe charges.

Design choice (v1): **no scheduler logic here**. Stripe Smart Retries
owns the retry cadence (attempts at ~0d, +3d, +7d). We just fire one
Resend notification per ``invoice.payment_failed`` webhook so the
cleaner sees the failure in their inbox and can update their card via
the Stripe Billing Portal. If Stripe gives up after the final attempt,
``webhooks._handle_invoice_payment_failed`` flips ``clients.active`` to
``false`` — suspending brief delivery — before this email goes out.
"""
from __future__ import annotations

import os
from typing import Any

from ..db import connect
from ..send.email import send_email


def _billing_portal_url() -> str:
    """Where the dunning email CTA points.

    Set ``BILLING_PORTAL_URL`` to a Stripe Customer Portal link
    (``billing.stripe.com/p/login/...``). Falls back to a generic
    Stripe URL only in dev so the link is never empty.
    """
    return os.environ.get("BILLING_PORTAL_URL", "https://billing.stripe.com/p/login")


def _lookup_client(client_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, name, contact_email FROM clients WHERE id = %s",
            (client_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "contact_email": row[2]}


def _subject(attempt_number: int) -> str:
    if attempt_number <= 1:
        return "Your MondayBrief payment didn't go through"
    if attempt_number == 2:
        return "Reminder: MondayBrief payment still pending"
    return "Final notice: MondayBrief payment failed — brief paused"


def _html_body(client_name: str, attempt_number: int, portal_url: str) -> str:
    if attempt_number <= 1:
        lead = (
            "We just tried to renew your MondayBrief subscription and the "
            "charge didn't go through. We'll automatically retry over the "
            "next few days, but updating your card now is the fastest fix."
        )
        footer = "Your Monday brief is still scheduled for now."
    elif attempt_number == 2:
        lead = (
            "Heads up — your MondayBrief payment is still pending. We'll "
            "try one more time. Please update your card before then so "
            "your brief keeps shipping."
        )
        footer = "Your Monday brief is still scheduled — for now."
    else:
        lead = (
            "We weren't able to renew your MondayBrief subscription after "
            "three attempts. Your weekly brief is paused until we can "
            "process payment."
        )
        footer = (
            "Update your card and your next Monday brief will resume the "
            "following week."
        )

    return (
        f"<p>Hi {client_name} team,</p>"
        f"<p>{lead}</p>"
        f'<p><a href="{portal_url}" style="display:inline-block;'
        f"padding:12px 20px;background:#111;color:#fff;text-decoration:none;"
        f'border-radius:4px;">Update payment method</a></p>'
        f"<p>{footer}</p>"
        f"<p>— MondayBrief</p>"
    )


def _text_body(client_name: str, attempt_number: int, portal_url: str) -> str:
    if attempt_number <= 1:
        lead = (
            "We just tried to renew your MondayBrief subscription and the "
            "charge didn't go through. We'll automatically retry over the "
            "next few days."
        )
    elif attempt_number == 2:
        lead = (
            "Your MondayBrief payment is still pending. We'll try one "
            "more time. Please update your card before then."
        )
    else:
        lead = (
            "We weren't able to renew your MondayBrief subscription "
            "after three attempts. Your weekly brief is paused until "
            "payment goes through."
        )

    return (
        f"Hi {client_name} team,\n\n"
        f"{lead}\n\n"
        f"Update payment method: {portal_url}\n\n"
        f"— MondayBrief\n"
    )


def send_dunning_email(client_id: str, attempt_number: int) -> str:
    """Send one payment-failure notification for ``client_id``.

    ``attempt_number`` is Stripe's 1-indexed ``invoice.attempt_count``.
    Copy escalates: gentle on 1, firmer on 2, "we paused you" on 3+.
    Returns the Resend message id (empty string if the send was dropped).
    Also logs a ``dunning_sent`` row to ``email_events`` for the audit trail.
    """
    client = _lookup_client(client_id)
    if client is None:
        return ""

    portal_url = _billing_portal_url()

    message_id = send_email(
        to=client["contact_email"],
        subject=_subject(attempt_number),
        html=_html_body(client["name"], attempt_number, portal_url),
        text=_text_body(client["name"], attempt_number, portal_url),
    )

    # Audit-trail row so we can answer "did we ever tell them?".
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO email_events
                (client_id, event_type, postmark_message_id, payload)
            VALUES (%s, 'dunning_sent', %s, %s::jsonb)
            """,
            (
                client_id,
                message_id,
                f'{{"attempt_number": {attempt_number}}}',
            ),
        )

    return message_id
