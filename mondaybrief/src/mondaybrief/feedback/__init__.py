"""Feedback loop — thumbs up/down capture from brief PDFs.

Each lead in a rendered brief carries a signed URL that maps back to
(scored_lead_id, client_id). A click writes one row to `lead_feedback`.
v1 only collects; future scoring iterations train on this signal.
"""
from .tokens import generate_feedback_token, verify_feedback_token
from .api import router

__all__ = ["generate_feedback_token", "verify_feedback_token", "router"]
