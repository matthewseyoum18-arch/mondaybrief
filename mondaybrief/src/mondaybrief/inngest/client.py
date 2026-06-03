"""Inngest client + functions for MondayBrief.

Two functions are registered with Inngest:

  1. ``pipeline.weekly-brief`` — cron trigger ``TZ=America/Chicago 0 7 * * 1``
     (Monday 7am Chicago). Queries the ``clients`` table for active clients
     and fans out one ``pipeline.run.requested`` event per client_id.

  2. ``pipeline.run`` — triggered on the ``pipeline.run.requested`` event.
     Executes the pipeline for the requested client_id inside a
     ``step.run`` (so Inngest can retry on failure) and, on exception,
     sends a Postmark alert email to the operator (``OPERATOR_EMAIL``).

The pipeline call is intentionally a thin wrapper around
``mondaybrief.pipeline.run`` invoked with ``client_id=`` as a kwarg.
A later phase will refactor ``pipeline.run`` to accept that kwarg
natively; this module does not modify ``pipeline.py``.
"""
from __future__ import annotations

import os
import traceback
from typing import Any

import inngest

from .. import pipeline as pipeline_module


# ---------------------------------------------------------------------------
# Inngest client
# ---------------------------------------------------------------------------

# Inngest reads INNGEST_SIGNING_KEY and INNGEST_EVENT_KEY from the environment
# by default, but we pass them explicitly so the wiring is visible at the
# call site. `is_production` flips on whenever a signing key is present —
# in local dev (Inngest Dev Server) it stays False so unsigned calls work.
_SIGNING_KEY = os.environ.get("INNGEST_SIGNING_KEY", "")
_EVENT_KEY = os.environ.get("INNGEST_EVENT_KEY", "")

inngest_client = inngest.Inngest(
    app_id="mondaybrief",
    signing_key=_SIGNING_KEY or None,
    event_key=_EVENT_KEY or None,
    is_production=bool(_SIGNING_KEY),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_active_client_ids() -> list[str]:
    """Return the active client UUIDs to fan out to on the Monday cron.

    We emit ``id::text`` (the UUID), NOT ``slug``: ``pipeline.run`` routes a
    UUID to the production DB/Stripe/Postmark path and routes a non-UUID slug
    to the offline smoke path. Emitting slugs would mean the weekly cron never
    actually runs/sends for real clients.
    """
    from ..db import execute  # local import to avoid DB connect at import time

    rows = execute("SELECT id::text FROM clients WHERE active = true ORDER BY slug")
    return [r[0] for r in rows]


def _alert_operator(client_id: str, error: BaseException) -> None:
    """Send a Resend alert email to the operator when a pipeline run fails.

    Uses the shared transport ``mondaybrief.send.email.send_email`` so we share
    the same sender identity as the normal brief path. Best-effort — if
    alerting itself fails, we swallow the error (Inngest already has the
    original exception in its run history).
    """
    operator = os.environ.get("OPERATOR_EMAIL")
    if not operator:
        return

    try:
        from ..send.email import send_email

        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        subject = f"[MondayBrief] pipeline.run failed for {client_id}"
        body_text = (
            f"Pipeline run for client_id={client_id} raised an exception:\n\n"
            f"{type(error).__name__}: {error}\n\n"
            f"Traceback:\n{tb}\n"
        )
        send_email(
            to=operator,
            subject=subject,
            html=f"<pre>{body_text}</pre>",
            text=body_text,
        )
    except Exception:
        # Alerting must never mask the original error.
        pass


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

@inngest_client.create_function(
    fn_id="pipeline.weekly-brief",
    trigger=inngest.TriggerCron(cron="TZ=America/Chicago 0 7 * * 1"),
)
async def weekly_brief(ctx: inngest.Context) -> dict[str, Any]:
    """Monday 7am Chicago — fan out one event per active client.

    The fan-out lives inside a ``step.run`` so Inngest can retry the DB
    read independently from the downstream per-client runs.
    """

    async def _load_clients() -> list[str]:
        return _fetch_active_client_ids()

    client_ids: list[str] = await ctx.step.run("load-active-clients", _load_clients)

    events = [
        inngest.Event(
            name="pipeline.run.requested",
            data={"client_id": cid},
        )
        for cid in client_ids
    ]

    if events:
        await ctx.step.send_event("fanout-pipeline-runs", events)

    return {"fanned_out": len(events), "client_ids": client_ids}


@inngest_client.create_function(
    fn_id="pipeline.run",
    trigger=inngest.TriggerEvent(event="pipeline.run.requested"),
    retries=2,
)
async def run_pipeline_for_client(ctx: inngest.Context) -> dict[str, Any]:
    """Per-client pipeline execution. Wraps ``pipeline.run`` in ``step.run``.

    ``pipeline.run(client_id=...)`` does not exist yet — the refactor agent
    will add it. We call it as a kwarg here so the contract is locked in
    place from day one.
    """
    client_id = ctx.event.data.get("client_id")
    if not client_id:
        raise ValueError("pipeline.run.requested event missing client_id")

    def _execute() -> dict[str, Any]:
        try:
            result = pipeline_module.run(client_id=client_id)  # type: ignore[attr-defined]
            return {"client_id": client_id, "ok": True, "result": repr(result)}
        except BaseException as exc:
            _alert_operator(client_id, exc)
            raise

    return await ctx.step.run(f"pipeline-run-{client_id}", _execute)


# Convenience export — server.py registers exactly these.
FUNCTIONS = [weekly_brief, run_pipeline_for_client]
