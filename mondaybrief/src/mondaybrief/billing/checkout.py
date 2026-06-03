"""Stripe Checkout entrypoint (FastAPI router).

``billing.stripe_client.create_checkout_session`` already builds the $149/mo
subscription session; this router is the missing HTTP surface that lets the
onboarding UI actually start a checkout. Mounted at ``POST /billing/checkout``.

Request body::

    {"success_url": "... optional same-origin billing success URL ..."}

The caller must also pass a signed session token from ``auth.magic_link``::

    Authorization: Bearer <token>

Returns the hosted Checkout URL the client is redirected to::

    {"checkout_url": "https://checkout.stripe.com/c/pay/cs_..."}

Success/cancel return URLs default to ``APP_BASE_URL``. Overrides are allowed
only when they stay on the configured origin and on the billing return paths.
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..auth.magic_link import verify_link
from ..config import get_settings
from ..db import execute
from .stripe_client import create_checkout_session

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutRequest(BaseModel):
    success_url: str | None = None
    cancel_url: str | None = None


class CheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str


def _client_from_bearer(authorization: str) -> dict:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="missing checkout session")

    client_id = verify_link(token.strip())
    if not client_id:
        raise HTTPException(status_code=401, detail="invalid or expired checkout session")

    rows = execute(
        """
        SELECT id, contact_email, active
          FROM clients
         WHERE id = %s
         LIMIT 1
        """,
        (client_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="client not found")

    row = rows[0]
    if not bool(row[2]):
        raise HTTPException(status_code=403, detail="client is inactive")

    return {"id": UUID(str(row[0])), "email": row[1]}


def _safe_return_url(candidate: str | None, *, default_url: str, allowed_path: str) -> str:
    if not candidate:
        return default_url

    base = get_settings().app_base_url.rstrip("/")
    base_parts = urlparse(base)
    parsed = urlparse(candidate)

    if not parsed.scheme and not parsed.netloc:
        if not candidate.startswith("/"):
            raise HTTPException(status_code=400, detail="return URL must be absolute or root-relative")
        candidate = urljoin(f"{base}/", candidate.lstrip("/"))
        parsed = urlparse(candidate)

    if (parsed.scheme, parsed.netloc) != (base_parts.scheme, base_parts.netloc):
        raise HTTPException(status_code=400, detail="return URL origin is not allowed")
    if parsed.path != allowed_path:
        raise HTTPException(status_code=400, detail="return URL path is not allowed")
    return candidate


@router.post("/checkout", response_model=CheckoutResponse)
def start_checkout(
    body: CheckoutRequest,
    authorization: str = Header(default="", alias="Authorization"),
) -> CheckoutResponse:
    base = get_settings().app_base_url.rstrip("/")
    client = _client_from_bearer(authorization)
    success_url = _safe_return_url(
        body.success_url,
        default_url=f"{base}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        allowed_path="/billing/success",
    )
    cancel_url = _safe_return_url(
        body.cancel_url,
        default_url=f"{base}/billing/cancel",
        allowed_path="/billing/cancel",
    )

    try:
        session = create_checkout_session(
            client_id=client["id"],
            client_email=str(client["email"]),
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except RuntimeError as exc:
        # Missing STRIPE_SECRET_KEY / STRIPE_PRICE_ID_MONTHLY — surface as a
        # 503 so the caller knows it's a provisioning gap, not a bad request.
        raise HTTPException(status_code=503, detail=str(exc))

    return CheckoutResponse(checkout_url=session.url, session_id=session.id)
