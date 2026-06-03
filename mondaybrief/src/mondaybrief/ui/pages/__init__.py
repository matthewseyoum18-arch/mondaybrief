"""Streamlit pages — each module exposes a ``render(client_id: str)`` fn."""

from . import past_briefs, upload_customers

__all__ = ["past_briefs", "upload_customers"]
