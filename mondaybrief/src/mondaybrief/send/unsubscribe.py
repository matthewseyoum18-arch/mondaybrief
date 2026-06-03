"""One-click email unsubscribe (CAN-SPAM + RFC 8058).

Each brief carries a ``List-Unsubscribe`` header pointing at
``{APP_BASE_URL}/unsubscribe/{token}`` plus ``List-Unsubscribe-Post:
List-Unsubscribe=One-Click`` so Gmail/Apple Mail can suppress with a single
POST and no landing page.

The token is an HMAC-signed ``{email, client_id}`` payload (itsdangerous,
same library as feedback tokens) so the URL can't be forged to unsubscribe
an arbitrary address. We reuse ``FEEDBACK_TOKEN_SECRET`` with a distinct
salt so there's only one secret to provision.

Both verbs land here:

* ``POST /unsubscribe/{token}`` — RFC 8058 one-click. Suppresses, returns 200.
* ``GET  /unsubscribe/{token}``  — human clicks the link; suppresses and
  renders a tiny confirmation page.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from ..config import get_settings
from .suppression import add_suppression

router = APIRouter(prefix="/unsubscribe", tags=["unsubscribe"])

# No expiry on unsubscribe links — an unsubscribe must work forever, even
# off a months-old email. (We still sign so the address can't be forged.)
_SALT = "mondaybrief.unsubscribe.v1"


def _serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get("FEEDBACK_TOKEN_SECRET", "")
    if not secret:
        raise RuntimeError(
            "FEEDBACK_TOKEN_SECRET is not set — refusing to sign unsubscribe "
            "tokens with an empty key."
        )
    return URLSafeTimedSerializer(secret_key=secret, salt=_SALT)


def generate_unsubscribe_token(email: str, client_id: str | None = None) -> str:
    """Signed, URL-safe token binding an unsubscribe action to one address."""
    return _serializer().dumps({"email": email, "client_id": client_id})


def verify_unsubscribe_token(token: str) -> Optional[dict]:
    """Decode a token. Returns ``{email, client_id}`` or None on bad signature."""
    if not token:
        return None
    try:
        payload = _serializer().loads(token)  # no max_age — never expires
    except BadSignature:
        return None
    except Exception:
        return None
    if not isinstance(payload, dict) or "email" not in payload:
        return None
    return {"email": str(payload["email"]), "client_id": payload.get("client_id")}


def unsubscribe_url(email: str, client_id: str | None = None) -> str:
    """Absolute URL embedded in the brief's List-Unsubscribe header + footer."""
    base = get_settings().app_base_url.rstrip("/")
    return f"{base}/unsubscribe/{generate_unsubscribe_token(email, client_id)}"


def _suppress_from_token(token: str) -> bool:
    payload = verify_unsubscribe_token(token)
    if payload is None:
        return False
    add_suppression(
        payload["email"],
        reason="unsubscribe",
        source="unsubscribe_link",
        client_id=payload.get("client_id"),
    )
    return True


@router.post("/{token}")
async def unsubscribe_one_click(token: str) -> PlainTextResponse:
    """RFC 8058 one-click. Mail clients POST here with no body."""
    ok = _suppress_from_token(token)
    if not ok:
        return PlainTextResponse("invalid unsubscribe link", status_code=400)
    return PlainTextResponse("unsubscribed", status_code=200)


@router.get("/{token}", response_class=HTMLResponse)
async def unsubscribe_page(token: str) -> HTMLResponse:
    """Human-facing unsubscribe confirmation."""
    ok = _suppress_from_token(token)
    if not ok:
        body = (
            "<h1>This unsubscribe link is invalid.</h1>"
            "<p>Reply to any brief email and we'll remove you manually.</p>"
        )
        status = 400
    else:
        body = (
            "<h1>You're unsubscribed.</h1>"
            "<p>You won't receive any more Monday briefs at this address. "
            "Changed your mind? Reply to a past brief and we'll turn it back on.</p>"
        )
        status = 200
    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Unsubscribe — MondayBrief</title>"
        "<style>body{font-family:-apple-system,sans-serif;max-width:480px;"
        "margin:48px auto;padding:0 16px;color:#1a1a1a;line-height:1.5}"
        "h1{font-size:20pt}</style></head><body>"
        f"{body}</body></html>"
    )
    return HTMLResponse(content=page, status_code=status)
