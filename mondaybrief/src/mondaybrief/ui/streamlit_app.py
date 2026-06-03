"""Streamlit entry point — sidebar nav + auth guard + page dispatch.

Run with::

    streamlit run src/mondaybrief/ui/streamlit_app.py

Auth model (per ``e2e v1.md`` dashboard section):
- Sign-in: enter email → we look up ``clients`` → Postmark sends a magic link.
- Sign-in URL carries ``?token=...``; on first load we verify + stash
  ``client_id`` in ``st.session_state``.
- All pages require ``client_id`` in session; otherwise the sign-in form
  renders inline.

Pages are owner-facing only: Upload Customers and Past Briefs. No admin
portal — that's explicitly out of scope.
"""
from __future__ import annotations

import streamlit as st

from ..auth.magic_link import send_magic_link, verify_link
from ..db import execute
from .pages import past_briefs, upload_customers


def _consume_token_from_url() -> None:
    """If the URL has ?token=..., verify it and write client_id to session."""
    params = st.query_params
    token = params.get("token")
    if not token:
        return
    # Streamlit sometimes returns a list; normalize.
    if isinstance(token, list):
        token = token[0] if token else None
    if not token:
        return

    client_id = verify_link(token)
    if client_id:
        st.session_state["client_id"] = client_id
        # Cache the client name so we can show it in the sidebar.
        rows = execute(
            "SELECT name, contact_email FROM clients WHERE id = %(id)s",
            {"id": client_id},
        )
        if rows:
            st.session_state["client_name"] = rows[0][0]
            st.session_state["client_email"] = rows[0][1]
        # Clear the token from the URL so refresh doesn't re-consume it.
        st.query_params.clear()
        st.rerun()
    else:
        st.session_state["auth_error"] = (
            "That sign-in link is invalid or expired. Request a new one below."
        )


def _render_sign_in() -> None:
    st.title("MondayBrief")
    st.caption("Sign in to your weekly lead-brief dashboard.")

    err = st.session_state.pop("auth_error", None)
    if err:
        st.error(err)

    with st.form("sign_in_form", clear_on_submit=True):
        email = st.text_input(
            "Your work email",
            placeholder="owner@cleaningco.com",
            help="The email tied to your MondayBrief account.",
        )
        submitted = st.form_submit_button("Email me a sign-in link", type="primary")

    if submitted:
        if not email or "@" not in email:
            st.error("Please enter a valid email address.")
            return
        try:
            send_magic_link(email.strip())
        except Exception as exc:  # noqa: BLE001 - surface infra errors to operator
            st.error(f"Could not send sign-in link: {exc}")
            return
        # Anti-enumeration: always show the same success message.
        st.success(
            "If that email is on a MondayBrief account, a sign-in link is on the way. "
            "Check your inbox (and spam folder) within a minute."
        )


def _render_sidebar() -> str:
    client_name = st.session_state.get("client_name", "your account")
    st.sidebar.markdown(f"**Signed in as**\n\n{client_name}")
    st.sidebar.divider()
    page = st.sidebar.radio(
        "Navigate",
        options=["Upload Customers", "Past Briefs", "Sign Out"],
        index=0,
        label_visibility="collapsed",
    )
    return page


def _sign_out() -> None:
    for key in ("client_id", "client_name", "client_email"):
        st.session_state.pop(key, None)
    st.query_params.clear()
    st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="MondayBrief",
        page_icon=":briefcase:",
        layout="wide",
    )

    # Always run token consumption first so an inbound magic link logs the
    # user in even if they had no prior session.
    if "client_id" not in st.session_state:
        _consume_token_from_url()

    if "client_id" not in st.session_state:
        _render_sign_in()
        return

    page = _render_sidebar()

    if page == "Upload Customers":
        upload_customers.render(client_id=st.session_state["client_id"])
    elif page == "Past Briefs":
        past_briefs.render(client_id=st.session_state["client_id"])
    elif page == "Sign Out":
        _sign_out()


if __name__ == "__main__":
    main()
