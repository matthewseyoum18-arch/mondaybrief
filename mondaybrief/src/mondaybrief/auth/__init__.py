"""Magic-link auth for the Streamlit dashboard.

Single signed-token flow — no passwords, no OAuth, no Lucia (banned per the
OSS no-go list). The Postmark transactional stream that delivers the brief
also delivers the sign-in link, so we get to reuse the warmed sender.
"""
from .magic_link import generate_link, verify_link, send_magic_link

__all__ = ["generate_link", "verify_link", "send_magic_link"]
