"""Inngest cron + event-driven orchestration for MondayBrief.

This package wires the weekly Monday 7am Chicago trigger and the per-client
fan-out function that actually executes the pipeline. Import paths:

    from mondaybrief.inngest.client import inngest_client, weekly_brief, run_pipeline_for_client
    from mondaybrief.inngest.server import app  # FastAPI app serving /api/inngest
"""
from __future__ import annotations

from .client import (
    inngest_client,
    weekly_brief,
    run_pipeline_for_client,
    FUNCTIONS,
)

__all__ = [
    "inngest_client",
    "weekly_brief",
    "run_pipeline_for_client",
    "FUNCTIONS",
]
