"""PDF rendering via WeasyPrint.

Repos:
  - WeasyPrint: https://github.com/Kozea/WeasyPrint (BSD-3)
  - Jinja2: https://github.com/pallets/jinja (BSD-3)

A Jinja2 HTML template gets the BriefBundle context, WeasyPrint compiles
to PDF with paged-media CSS. Output is a 3-page brief.

ODbL attribution rendered in the footer template — required because the
drive-time math derives from OpenStreetMap-fed routing.

Feedback links: when `scored_lead_ids` is provided (one ID per lead, same
order as `bundle.leads`), each lead row gets a signed token URL that
points at the FastAPI feedback endpoint. If IDs aren't available (e.g.
offline smoke tests before DB insert), the feedback links are omitted.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Sequence

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..feedback.tokens import generate_feedback_token
from ..models import BriefBundle

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )


def _build_feedback_tokens(
    scored_lead_ids: Optional[Sequence[int]],
    client_id: Optional[str],
) -> list[Optional[str]]:
    """One token per lead, or None when we can't sign (offline mode etc.).

    We swallow signing errors here on purpose — a missing FEEDBACK_TOKEN_SECRET
    should not break PDF generation; the PDF just renders without feedback
    links and the pipeline keeps going.
    """
    if not scored_lead_ids or not client_id:
        return []
    tokens: list[Optional[str]] = []
    for sid in scored_lead_ids:
        try:
            tokens.append(generate_feedback_token(sid, client_id))
        except Exception:
            tokens.append(None)
    return tokens


def render_html(
    bundle: BriefBundle,
    scored_lead_ids: Optional[Sequence[int]] = None,
    client_uuid: Optional[str] = None,
) -> str:
    """Render the brief to an HTML string.

    `scored_lead_ids` must align 1:1 with `bundle.leads` if provided.
    `client_uuid` is the UUID of the client row used to sign tokens; falls
    back to bundle.client_id when omitted (legacy slug-keyed callers).
    """
    template = _env().get_template("brief.html")
    cid = client_uuid or bundle.client_id
    feedback_tokens = _build_feedback_tokens(scored_lead_ids, cid)
    app_base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    return template.render(
        bundle=bundle,
        feedback_tokens=feedback_tokens,
        app_base_url=app_base_url,
    )


def render_pdf(
    bundle: BriefBundle,
    out_path: Path | str,
    scored_lead_ids: Optional[Sequence[int]] = None,
    client_uuid: Optional[str] = None,
) -> Path:
    """Render the BriefBundle to a PDF at out_path. Returns the resolved Path."""
    # Lazy import: WeasyPrint pulls native GTK/Pango libs at import time. Keeping
    # it out of module scope means importing the pipeline (and collecting tests)
    # works on a box without those system libs; only an actual PDF render needs them.
    from weasyprint import HTML

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(bundle, scored_lead_ids=scored_lead_ids, client_uuid=client_uuid)
    HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(str(out))
    return out
