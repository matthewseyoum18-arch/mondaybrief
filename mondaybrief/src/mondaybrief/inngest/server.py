"""FastAPI server exposing the Inngest webhook endpoint.

Run locally (with the Inngest Dev Server discovering it):

    uvicorn mondaybrief.inngest.server:app --reload --port 8288

The Inngest Dev Server auto-discovers ``/api/inngest`` and registers both
``pipeline.weekly-brief`` (Monday 7am Chicago cron) and ``pipeline.run``
(event-triggered per-client execution).
"""
from __future__ import annotations

from fastapi import FastAPI
import inngest.fast_api

from .client import inngest_client, FUNCTIONS


app = FastAPI(title="MondayBrief Inngest")


inngest.fast_api.serve(app, inngest_client, FUNCTIONS)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Lightweight liveness check separate from the Inngest handler."""
    return {"status": "ok"}
