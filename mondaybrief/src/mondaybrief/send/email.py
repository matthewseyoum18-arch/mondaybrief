"""Resend transactional send — the single email transport for the app.

Every outbound email (weekly brief, magic-link sign-in, dunning, operator
alerts) goes through :func:`send_email`. We use Resend (https://resend.com)
via its Python SDK; deliverability and reputation are managed on the verified
sending domain configured in the Resend dashboard.

Repos / SDKs:
  - resend-python: https://github.com/resend/resend-python (MIT)
"""
from __future__ import annotations

from typing import Optional, Sequence

import resend

from ..config import get_settings


def send_email(
    *,
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
    attachments: Optional[Sequence[dict]] = None,
    tags: Optional[Sequence[dict]] = None,
    from_email: Optional[str] = None,
) -> str:
    """Send one email through Resend. Returns the Resend message id ('' if none).

    ``tags`` are Resend ``[{"name": ..., "value": ...}]`` entries (name/value
    must match ``^[A-Za-z0-9_-]+$``); they flow back on the webhook for
    attribution. ``attachments`` are Resend ``[{"filename", "content"}]`` dicts
    where ``content`` is a list of byte values.
    """
    settings = get_settings()
    resend.api_key = settings.resend_api_key

    params: dict = {
        "from": from_email or settings.resend_from_email,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text is not None:
        params["text"] = text
    if headers:
        params["headers"] = dict(headers)
    if attachments:
        params["attachments"] = list(attachments)
    if tags:
        params["tags"] = list(tags)

    resp = resend.Emails.send(params)
    if isinstance(resp, dict):
        return str(resp.get("id", "") or "")
    return str(getattr(resp, "id", "") or "")
