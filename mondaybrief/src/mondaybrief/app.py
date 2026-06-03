"""FastAPI host — the public HTTP surface (in-package, importable).

This module lives *inside* the installed package so it works with a clean
uvicorn import path regardless of CWD::

    uvicorn mondaybrief.app:app --host 0.0.0.0 --port $PORT

(The repo-root ``main.py`` is a thin shim that re-exports ``app`` from here
so ``python main.py`` and older deploy configs keep working.)

Mounted surfaces:

* ``POST /webhooks/stripe``    — billing.webhooks (Stripe subscription events)
* ``POST /webhooks/resend``    — observability.resend_webhook (delivery telemetry)
* ``POST /billing/checkout``   — billing.checkout (create a Stripe Checkout session)
* ``GET  /feedback/{token}``   — feedback.api (per-lead thumbs from the PDF links)
* ``GET  /unsubscribe/{token}``— send.unsubscribe (one-click CAN-SPAM opt-out)
* ``GET  /healthz``            — liveness probe
* ``GET  /``                   — tiny landing page

Inngest functions live in a separate process (``mondaybrief.inngest.server``)
so the cron handler scales independently of this webhook surface.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from mondaybrief.billing.checkout import router as checkout_router
from mondaybrief.billing.webhooks import router as stripe_router
from mondaybrief.feedback.api import router as feedback_router
from mondaybrief.observability.resend_webhook import router as resend_router
from mondaybrief.send.unsubscribe import router as unsubscribe_router

app = FastAPI(
    title="MondayBrief",
    description=(
        "Public HTTP surface: Stripe webhooks + checkout, Resend delivery "
        "webhooks, per-lead PDF feedback links, and email unsubscribe."
    ),
    version="1.0.0",
)

app.include_router(stripe_router)
app.include_router(checkout_router)
app.include_router(resend_router)
app.include_router(feedback_router)
app.include_router(unsubscribe_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe. Returns 200 OK without touching the DB."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Tiny landing page so the root URL doesn't 404 in browser tabs."""
    body = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>MondayBrief</title>"
        "<style>body{font-family:-apple-system,sans-serif;max-width:520px;"
        "margin:60px auto;padding:0 16px;color:#1a1a1a;line-height:1.5}"
        "h1{font-size:22pt;margin:0 0 6px}.muted{color:#666;font-size:10pt}</style>"
        "</head><body>"
        "<h1>MondayBrief</h1>"
        "<p class='muted'>Monday 7am lead briefs for commercial cleaners.</p>"
        "<p>This domain hosts the public HTTP surface — webhooks and feedback "
        "links. The owner dashboard lives separately on Streamlit.</p>"
        "</body></html>"
    )
    return HTMLResponse(content=body)
