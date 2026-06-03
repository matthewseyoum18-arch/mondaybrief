"""Resend webhook receiver (Svix-signed).

Resend posts JSON for each email event (``email.sent``, ``email.delivered``,
``email.bounced``, ``email.complained``, ``email.opened``, ``email.clicked``,
``email.delivery_delayed``). We verify the Svix signature with
``RESEND_WEBHOOK_SECRET``, log the raw event to ``email_events`` keyed by the
Resend ``email_id``, and feed hard bounces + spam complaints into the
suppression list.

Mount in FastAPI app::

    from mondaybrief.observability.resend_webhook import router
    app.include_router(router)
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, status

from ..config import get_settings
from ..db import connect
from ..send.suppression import add_suppression

router = APIRouter()


def _verify_signature(body: bytes, headers: dict[str, str]) -> dict:
    """Verify the Svix signature and return the parsed event.

    Refuses (503) when no secret is configured rather than accepting an
    unauthenticated write to ``email_events`` / the suppression list.
    """
    secret = get_settings().resend_webhook_secret
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RESEND_WEBHOOK_SECRET not configured",
        )
    from svix.webhooks import Webhook, WebhookVerificationError

    try:
        return Webhook(secret).verify(body, headers)
    except WebhookVerificationError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad signature")


def _recipient(data: dict[str, Any]) -> str:
    to = data.get("to")
    if isinstance(to, list):
        return (to[0] if to else "").strip()
    if isinstance(to, str):
        return to.strip()
    return ""


def _suppression_for_event(event: dict[str, Any]) -> Optional[tuple[str, str]]:
    """Suppression policy as a pure function (no DB), so it's unit-testable.

    Returns ``(email, reason)`` when the event means we must never send to the
    recipient again, else ``None``. Suppress on spam complaints and *permanent*
    (hard) bounces. Transient bounces, opens, deliveries, and sends never
    suppress. (Unsubscribes arrive on our own ``/unsubscribe`` route via the
    List-Unsubscribe header, not as a Resend webhook event.)
    """
    etype = event.get("type", "")
    data = event.get("data") or {}
    email = _recipient(data)
    if not email:
        return None
    if etype == "email.complained":
        return email, "spam_complaint"
    if etype == "email.bounced":
        bounce = data.get("bounce") or {}
        btype = str(bounce.get("type") or data.get("type") or "").lower()
        if btype in ("permanent", "hardbounce", "hard"):
            return email, "hard_bounce"
    return None


@router.post("/webhooks/resend")
async def resend_webhook(request: Request) -> dict[str, Any]:
    """Receive a Resend (Svix) webhook, log it, and suppress hard bounces/complaints."""
    body = await request.body()
    svix_headers = {
        "svix-id": request.headers.get("svix-id", ""),
        "svix-timestamp": request.headers.get("svix-timestamp", ""),
        "svix-signature": request.headers.get("svix-signature", ""),
    }
    event = _verify_signature(body, svix_headers)

    event_type = event.get("type", "unknown")
    data = event.get("data") or {}
    resend_message_id = data.get("email_id") or data.get("id")

    # Resend echoes the email's tags in the webhook payload as {name: value}.
    tags = data.get("tags") or {}
    client_uuid = tags.get("client_id") if isinstance(tags, dict) else None
    pipeline_run_id = tags.get("pipeline_run_id") if isinstance(tags, dict) else None

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO email_events
              (pipeline_run_id, client_id, event_type, postmark_message_id, payload)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            """,
            (
                int(pipeline_run_id) if pipeline_run_id else None,
                client_uuid,
                event_type,
                resend_message_id,
                json.dumps(event),
            ),
        )

    # email_events is the durable audit log, committed above. Apply suppression
    # on its own connection, best-effort: a transient suppression-write failure
    # must not roll back the event log or 500 the webhook (Resend would retry
    # and double-log). The next send re-reads the suppression list
    # (is_suppressed fails closed), so a missed write is caught at send time.
    suppression = _suppression_for_event(event)
    if suppression:
        try:
            add_suppression(
                suppression[0],
                reason=suppression[1],
                source="resend_webhook",
                client_id=client_uuid,
            )
        except Exception:
            pass

    return {"status": "ok", "event_type": event_type, "message_id": resend_message_id}
