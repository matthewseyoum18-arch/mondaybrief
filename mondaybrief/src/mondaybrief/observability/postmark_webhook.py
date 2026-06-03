"""Postmark webhook receiver.

Postmark posts JSON to a single URL for every email event (Delivery,
Bounce, Open, Click, SpamComplaint, SubscriptionChange). We dump the
raw payload into ``email_events`` keyed by ``postmark_message_id`` so
the dashboard can show open / bounce rates per brief.

Auth is a shared bearer token — Postmark adds it to the URL or as a
``Authorization`` header depending on the config. We accept both.

Mount in FastAPI app::

    from fastapi import FastAPI
    from mondaybrief.observability.postmark_webhook import router

    app = FastAPI()
    app.include_router(router)
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from ..db import connect
from ..send.suppression import add_suppression

router = APIRouter()


_EVENT_TYPE_MAP = {
    "Delivery": "delivered",
    "Bounce": "bounced",
    "Open": "opened",
    "Click": "clicked",
    "SpamComplaint": "spam_complaint",
    "SubscriptionChange": "subscription_change",
}


def _expected_token() -> str:
    return os.environ.get("POSTMARK_WEBHOOK_TOKEN", "").strip()


def _verify_token(
    header_value: Optional[str],
    query_value: Optional[str],
) -> None:
    expected = _expected_token()
    if not expected:
        # If no token configured, refuse to accept webhooks rather than
        # silently allow anyone to write to email_events.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="POSTMARK_WEBHOOK_TOKEN not configured",
        )

    candidate: Optional[str] = None
    if header_value:
        # Accept "Bearer xxx" or raw token
        candidate = header_value.split(" ", 1)[1] if header_value.lower().startswith("bearer ") else header_value
    elif query_value:
        candidate = query_value

    if not candidate or candidate.strip() != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad token")


def _resolve_event_type(payload: dict[str, Any]) -> str:
    raw = payload.get("RecordType") or payload.get("Type") or ""
    return _EVENT_TYPE_MAP.get(raw, raw.lower() or "unknown")


def _suppression_for_event(event: dict[str, Any]) -> Optional[tuple[str, str]]:
    """Suppression policy as a pure function (no DB), so it's unit-testable.

    Returns ``(email, reason)`` when this Postmark event means we must never
    send to the recipient again, else ``None``. We suppress on spam complaints,
    *hard* bounces (permanent), and SubscriptionChange events that turn sending
    off. Soft bounces, opens, deliveries, and re-subscribes never suppress.
    """
    etype = _resolve_event_type(event)
    email = (event.get("Email") or event.get("Recipient") or "").strip()
    if not email:
        return None
    if etype == "spam_complaint":
        return email, "spam_complaint"
    if etype == "bounced" and event.get("Type") == "HardBounce":
        return email, "hard_bounce"
    if etype == "subscription_change" and event.get("SuppressSending") is True:
        return email, "unsubscribe"
    return None


@router.post("/webhooks/postmark")
async def postmark_webhook(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    """Receive a Postmark webhook and persist it to ``email_events``.

    Postmark wraps each event in a single JSON object; we accept either
    a single event or a list (some Postmark batched modes send a list).
    """
    _verify_token(authorization, token)

    body = await request.body()
    try:
        parsed = json.loads(body or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid json")

    events = parsed if isinstance(parsed, list) else [parsed]
    inserted: list[str] = []
    to_suppress: list[tuple[str, str, Optional[str]]] = []

    with connect() as conn:
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = _resolve_event_type(event)
            postmark_message_id = (
                event.get("MessageID")
                or event.get("MessageId")
                or event.get("ID")
            )

            # Best-effort: pull client_id and pipeline_run_id from the
            # Postmark Metadata block if our sender attached them.
            metadata = event.get("Metadata") or {}
            client_uuid = metadata.get("client_id")
            pipeline_run_id = metadata.get("pipeline_run_id")
            scored_lead_id = metadata.get("scored_lead_id")

            conn.execute(
                """
                INSERT INTO email_events
                  (scored_lead_id, pipeline_run_id, client_id, event_type,
                   postmark_message_id, payload)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    int(scored_lead_id) if scored_lead_id else None,
                    int(pipeline_run_id) if pipeline_run_id else None,
                    client_uuid,
                    event_type,
                    postmark_message_id,
                    json.dumps(event),
                ),
            )
            if postmark_message_id:
                inserted.append(postmark_message_id)

            # Defer the suppression write: collect now, apply after the event
            # log commits (below) so the audit insert can't be rolled back by a
            # suppression hiccup.
            suppression = _suppression_for_event(event)
            if suppression:
                to_suppress.append((suppression[0], suppression[1], client_uuid))

    # email_events is the durable audit log, committed above. Apply the derived
    # suppressions on their own connections, best-effort: a transient
    # suppression-write failure must not roll back the event log or 500 the
    # webhook (Postmark would retry and double-log). The next send re-reads the
    # suppression list (is_suppressed fails closed), so a missed write here is
    # caught at send time rather than letting a suppressed address through.
    for email, reason, client_uuid in to_suppress:
        try:
            add_suppression(
                email, reason=reason, source="postmark_webhook", client_id=client_uuid
            )
        except Exception:
            pass

    return {"status": "ok", "events_logged": len(inserted), "message_ids": inserted}
