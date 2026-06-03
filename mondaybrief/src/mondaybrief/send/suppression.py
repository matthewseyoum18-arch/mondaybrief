"""Recipient suppression list (CAN-SPAM / deliverability).

A row in ``email_suppressions`` means we must never send to that address.
The list is fed from three places:

* Postmark ``SpamComplaint`` and ``SubscriptionChange`` webhooks
  (``observability.postmark_webhook``).
* Postmark hard ``Bounce`` events (permanent failures).
* The one-click unsubscribe route (``send.unsubscribe``).

``is_suppressed`` is checked in ``send.postmark.send_brief`` right before
the API call, so a suppressed address short-circuits the send.

All emails are normalised to ``lower().strip()`` before storage/lookup so
casing can never let a suppressed address slip through.
"""
from __future__ import annotations

from ..db import connect


class SuppressionCheckError(RuntimeError):
    """Raised when the suppression list cannot be read (after one retry).

    Callers MUST treat this as "do not send" — emailing an address we failed
    to clear risks hitting an unsubscribed / complained recipient, which is a
    CAN-SPAM + sender-reputation failure worse than a delayed brief.
    """


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def is_suppressed(email: str) -> bool:
    """True if this address is on the suppression list.

    Fails *closed*: one transient DB error is retried; if the lookup still
    can't complete we raise ``SuppressionCheckError`` rather than returning
    False. Suppression is the last compliance guard before the send, so when
    we cannot prove an address is clear we must not send to it. The caller
    (``send_brief`` -> ``pipeline.run``) turns the raise into a failed run +
    operator alert instead of a silent send or a silent drop.
    """
    norm = normalize_email(email)
    if not norm:
        return False
    last_exc: Exception | None = None
    for _attempt in range(2):  # initial try + one retry on a transient blip
        try:
            with connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM email_suppressions WHERE email = %s",
                    (norm,),
                ).fetchone()
            return row is not None
        except Exception as exc:  # noqa: BLE001 - retry any DB-layer failure
            last_exc = exc
    raise SuppressionCheckError(
        f"suppression lookup failed for {norm!r} after retry; refusing to send"
    ) from last_exc


def add_suppression(
    email: str,
    *,
    reason: str,
    source: str | None = None,
    client_id: str | None = None,
) -> None:
    """Upsert an address onto the suppression list.

    Idempotent: re-suppressing keeps the earliest ``created_at`` and refreshes
    the reason/source so we always have the most recent cause on record.
    """
    norm = normalize_email(email)
    if not norm:
        return
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO email_suppressions (email, client_id, reason, source)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (email) DO UPDATE
              SET reason = EXCLUDED.reason,
                  source = EXCLUDED.source,
                  client_id = COALESCE(email_suppressions.client_id, EXCLUDED.client_id)
            """,
            (norm, client_id, reason, source),
        )


def remove_suppression(email: str) -> None:
    """Delete an address from the suppression list (manual re-subscribe)."""
    norm = normalize_email(email)
    if not norm:
        return
    with connect() as conn:
        conn.execute("DELETE FROM email_suppressions WHERE email = %s", (norm,))
