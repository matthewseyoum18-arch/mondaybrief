"""Signed feedback tokens.

We don't want lead IDs in raw URLs (scraping, replay, leakage). Instead
we sign a small payload {scored_lead_id, client_id} with HMAC via
itsdangerous (BSD-3, https://github.com/pallets/itsdangerous) and embed
the resulting opaque string in the PDF feedback links.

Tokens are valid for 30 days — long enough that a cleaner can mark up a
brief on Friday, short enough that stale links rot out.
"""
from __future__ import annotations

import os
from typing import Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# 30 days, matches expected brief shelf life.
TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60

# Namespace salt so this serializer can't be confused with any future one
# that happens to share FEEDBACK_TOKEN_SECRET.
_SALT = "mondaybrief.feedback.v1"


def _serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get("FEEDBACK_TOKEN_SECRET", "")
    if not secret:
        # Fail loud in prod; tests can set the env var explicitly.
        raise RuntimeError(
            "FEEDBACK_TOKEN_SECRET is not set — refusing to sign feedback tokens "
            "with an empty key."
        )
    return URLSafeTimedSerializer(secret_key=secret, salt=_SALT)


def generate_feedback_token(scored_lead_id: int, client_id: str) -> str:
    """Return a signed, URL-safe token binding a lead to a client.

    `client_id` is the UUID string of the client row (matches `clients.id`).
    `scored_lead_id` is the BIGINT PK of `scored_leads`.
    """
    payload = {
        "scored_lead_id": int(scored_lead_id),
        "client_id": str(client_id),
    }
    return _serializer().dumps(payload)


def verify_feedback_token(token: str) -> Optional[dict]:
    """Decode and validate a token.

    Returns {scored_lead_id: int, client_id: str} on success, None on any
    signature failure (bad sig, expired, malformed payload). Caller decides
    how to respond — we never raise on user input.
    """
    if not token:
        return None
    try:
        payload = _serializer().loads(token, max_age=TOKEN_TTL_SECONDS)
    except (SignatureExpired, BadSignature):
        return None
    except Exception:
        # Defensive: malformed base64, JSON, etc.
        return None

    if not isinstance(payload, dict):
        return None
    if "scored_lead_id" not in payload or "client_id" not in payload:
        return None
    try:
        return {
            "scored_lead_id": int(payload["scored_lead_id"]),
            "client_id": str(payload["client_id"]),
        }
    except (TypeError, ValueError):
        return None
