"""Observability helpers for MondayBrief.

Wires Langfuse traces around Claude calls, computes per-run cost totals
from per-step incremental costs, and exposes the Resend webhook router
that logs delivery events into ``email_events``.

Importing this package is side-effect-free: Langfuse is only initialised
when ``init_langfuse()`` is called, and the import of
:mod:`resend_webhook` is deferred so apps that don't need the webhook
don't pay the FastAPI import cost.
"""
from __future__ import annotations

from .langfuse_setup import init_langfuse, wrap_claude_call
from .cost import update_run_cost

__all__ = ["init_langfuse", "wrap_claude_call", "update_run_cost"]
