"""FastAPI router for the lead-feedback loop.

Mounted under `/feedback` from whatever app hosts the public surface
(separate from the Streamlit dashboard — this endpoint is reachable
without auth because the token is the auth).

Flow:
  1. Brief PDF renders a link `{APP_BASE_URL}/feedback/{token}?thumbs=up`
  2. Cleaner clicks it on their phone
  3. GET renders a tiny confirmation page with optional "note" textarea
  4. POST writes one `lead_feedback` row (insert-only — no updates,
     repeat clicks just add rows; downstream model dedups if it wants)

Schema reference: `mondaybrief/schema.sql` -> `lead_feedback` table.
Token format: see `tokens.py`.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from ..db import connect
from .tokens import verify_feedback_token

router = APIRouter(prefix="/feedback", tags=["feedback"])


# ---------- HTML rendering (no Jinja dependency — this is one tiny page)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — MondayBrief</title>
<style>
  body {{ font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; color: #1a1a1a;
          max-width: 480px; margin: 40px auto; padding: 0 16px; line-height: 1.5; }}
  h1 {{ font-size: 22pt; margin: 0 0 12px; }}
  .card {{ background: #f6f8fa; border-left: 3px solid #0a7d57; padding: 14px 18px;
           border-radius: 4px; margin: 18px 0; }}
  .row {{ display: flex; gap: 10px; margin: 14px 0; }}
  .btn {{ display: inline-block; padding: 10px 16px; border-radius: 6px;
          background: #0a7d57; color: #fff; text-decoration: none; border: 0;
          font-size: 14pt; cursor: pointer; }}
  .btn.alt {{ background: #b34a3a; }}
  .btn.ghost {{ background: #fff; color: #444; border: 1px solid #ccc; }}
  textarea {{ width: 100%; min-height: 90px; padding: 10px;
              border: 1px solid #ccc; border-radius: 4px; font: inherit; }}
  .muted {{ color: #666; font-size: 10pt; }}
  .pre-selected {{ font-weight: 700; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _form_html(token: str, preselected: Optional[str]) -> str:
    up_marker = " (selected)" if preselected == "up" else ""
    down_marker = " (selected)" if preselected == "down" else ""
    body = f"""
<h1>How was this lead?</h1>
<p class="muted">Your input trains next Monday's brief. One click, optional note.</p>

<form method="post" action="/feedback/{token}">
  <div class="row">
    <button class="btn" name="thumbs" value="up" type="submit">
      Good lead{up_marker}
    </button>
    <button class="btn alt" name="thumbs" value="down" type="submit">
      Skip{down_marker}
    </button>
  </div>
  <label class="muted" for="note">Optional note (why?):</label>
  <textarea id="note" name="note" placeholder="e.g. wrong category, too far, already a customer"></textarea>
</form>
"""
    return _page("Lead feedback", body)


def _thanks_html(thumbs: str) -> str:
    verdict = "Marked as a good lead." if thumbs == "up" else "Marked to skip."
    body = f"""
<h1>Thanks.</h1>
<div class="card">{verdict}</div>
<p class="muted">You can close this tab. Next Monday's brief will weight your feedback.</p>
"""
    return _page("Thanks", body)


def _invalid_html() -> str:
    body = """
<h1>This link has expired.</h1>
<p class="muted">Feedback links are valid for 30 days. If you still want to flag this lead,
reply to the brief email and we'll log it manually.</p>
"""
    return _page("Link expired", body)


# ---------- Routes


@router.get("/{token}", response_class=HTMLResponse)
async def feedback_form(
    token: str,
    thumbs: Optional[Literal["up", "down"]] = Query(default=None),
) -> HTMLResponse:
    """Render the tiny feedback form.

    The `thumbs` query param is only a UI hint (which button to pre-highlight)
    when the cleaner clicks "Good lead" / "Skip" from the PDF. The actual
    write happens on POST so we don't mutate state on a GET.
    """
    payload = verify_feedback_token(token)
    if payload is None:
        return HTMLResponse(content=_invalid_html(), status_code=410)
    return HTMLResponse(content=_form_html(token, thumbs))


@router.post("/{token}", response_class=HTMLResponse)
async def feedback_submit(
    request: Request,
    token: str,
    thumbs: Literal["up", "down"] = Form(...),
    note: Optional[str] = Form(default=None),
) -> HTMLResponse:
    """Persist one `lead_feedback` row keyed on the signed token."""
    payload = verify_feedback_token(token)
    if payload is None:
        raise HTTPException(status_code=410, detail="Feedback link expired or invalid.")

    scored_lead_id = payload["scored_lead_id"]
    client_id = payload["client_id"]

    # Trim note — empty string -> NULL so the column stays clean.
    note_clean: Optional[str] = (note or "").strip() or None

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO lead_feedback (scored_lead_id, client_id, thumbs, note)
            VALUES (%s, %s, %s, %s)
            """,
            (scored_lead_id, client_id, thumbs, note_clean),
        )

    return HTMLResponse(content=_thanks_html(thumbs))
