"""Signed-token magic-link auth.

Tokens are itsdangerous ``URLSafeTimedSerializer`` blobs containing the
client UUID. 30-minute TTL, single secret (``MAGIC_LINK_SECRET``). On verify
we re-derive the client_id from the token payload — no DB round-trip needed
to validate, only to render.

Email delivery reuses the shared Resend transport; we send from the same
``resend_from_email`` configured for the weekly brief.
"""
from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlencode

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..db import execute
from ..send.email import send_email


MAGIC_LINK_TTL_SECONDS = 30 * 60  # 30 minutes
_SALT = "mondaybrief-magic-link"


def _serializer() -> URLSafeTimedSerializer:
    secret = os.getenv("MAGIC_LINK_SECRET")
    if not secret:
        raise RuntimeError(
            "MAGIC_LINK_SECRET env var is required for magic-link auth. "
            "Generate one with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`."
        )
    return URLSafeTimedSerializer(secret_key=secret, salt=_SALT)


def generate_link(client_id: str, email: str) -> str:
    """Return a fully-qualified sign-in URL for ``client_id``.

    Token payload is ``{client_id, email}`` so we can render the email in
    ``verify_link`` audit logs without an extra DB hop.
    """
    base = os.getenv("APP_BASE_URL", "http://localhost:8501").rstrip("/")
    token = _serializer().dumps({"client_id": str(client_id), "email": email})
    return f"{base}/?{urlencode({'token': token})}"


def verify_link(token: str) -> Optional[str]:
    """Return ``client_id`` if token is valid + unexpired, else ``None``.

    Caller is responsible for setting ``st.session_state['client_id']``.
    """
    if not token:
        return None
    try:
        payload = _serializer().loads(token, max_age=MAGIC_LINK_TTL_SECONDS)
    except SignatureExpired:
        return None
    except BadSignature:
        return None
    client_id = payload.get("client_id") if isinstance(payload, dict) else None
    return str(client_id) if client_id else None


def send_magic_link(email: str) -> bool:
    """Look up the client by email and email them a sign-in link.

    Returns ``True`` on send, ``False`` if the email isn't on a client row.
    We don't tell the requester which case it was — that's anti-enumeration.
    """
    rows = execute(
        "SELECT id, name FROM clients WHERE LOWER(contact_email) = LOWER(%(email)s) AND active = true LIMIT 1",
        {"email": email},
    )
    if not rows:
        return False

    client_uuid, client_name = rows[0]
    url = generate_link(str(client_uuid), email)

    send_email(
        to=email,
        subject="Your MondayBrief sign-in link",
        text=(
            f"Hi {client_name} team,\n\n"
            f"Click the link below to sign in to your MondayBrief dashboard. "
            f"It expires in 30 minutes.\n\n"
            f"{url}\n\n"
            f"If you didn't request this, ignore the email — no account changes will be made.\n\n"
            f"— MondayBrief\n"
        ),
        html=(
            f"<p>Hi {client_name} team,</p>"
            f"<p>Click the button below to sign in to your MondayBrief dashboard. "
            f"This link expires in 30 minutes.</p>"
            f'<p><a href="{url}" style="background:#111;color:#fff;padding:12px 20px;'
            f'text-decoration:none;border-radius:6px;display:inline-block;">Sign in</a></p>'
            f'<p style="font-size:12px;color:#666;">'
            f"If the button doesn't work, paste this URL into your browser:<br/>{url}</p>"
            f'<p style="font-size:12px;color:#666;">If you didn\'t request this, ignore the email.</p>'
        ),
    )
    return True
