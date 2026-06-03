"""Postmark transactional send.

We use a separate Message Stream (``broadcast-monthly``) so the weekly brief
ships from a reputation lane isolated from any future newsletter traffic.
This is the deliverability lever the Production Stack Research called out.
"""
from __future__ import annotations
import base64
from pathlib import Path
from postmarker.core import PostmarkClient

from ..config import get_settings
from ..models import BriefBundle
from .suppression import is_suppressed
from .unsubscribe import unsubscribe_url


def _client() -> PostmarkClient:
    return PostmarkClient(server_token=get_settings().postmark_server_token)


def _attach_pdf(pdf_path: Path) -> dict:
    return {
        "Name": pdf_path.name,
        "Content": base64.b64encode(pdf_path.read_bytes()).decode("ascii"),
        "ContentType": "application/pdf",
    }


def send_brief(
    bundle: BriefBundle,
    pdf_path: Path,
    to_email: str,
    html_body_path: Path | None = None,
    *,
    client_id: str | None = None,
    pipeline_run_id: int | None = None,
) -> str:
    """Send the brief to a single recipient. Returns the Postmark message id.

    Returns an empty string without sending when ``to_email`` is on the
    suppression list (unsubscribed / spam complaint / hard bounce) — a
    suppressed address must never receive another brief (CAN-SPAM).
    """
    # Last compliance guard. is_suppressed fails closed: if the list can't be
    # read it raises SuppressionCheckError, which propagates up to pipeline.run
    # and aborts the run (operator alerted) rather than risk an unlawful send.
    if is_suppressed(to_email):
        return ""

    settings = get_settings()

    subject = (
        f"Monday brief — {bundle.client_name} — "
        f"{len(bundle.leads)} new leads, week of {bundle.week_of.strftime('%b %d')}"
    )
    unsub_url = unsubscribe_url(to_email, client_id)
    base_html = (
        html_body_path.read_text(encoding="utf-8") if html_body_path else _fallback_html(bundle)
    )
    html = base_html + _canspam_footer_html(unsub_url)
    text = _fallback_text(bundle) + _canspam_footer_text(unsub_url)

    # Metadata flows back on every Postmark webhook event so delivery/bounce
    # telemetry attributes to the right client + run. Drop unset keys.
    metadata = {
        k: str(v)
        for k, v in (("client_id", client_id), ("pipeline_run_id", pipeline_run_id))
        if v is not None
    }

    response = _client().emails.send(
        From=settings.postmark_from_email,
        To=to_email,
        Subject=subject,
        HtmlBody=html,
        TextBody=text,
        MessageStream=settings.postmark_message_stream,
        Attachments=[_attach_pdf(pdf_path)],
        TrackOpens=True,
        TrackLinks="HtmlOnly",
        Headers=[
            {"Name": "List-Unsubscribe", "Value": f"<{unsub_url}>"},
            {"Name": "List-Unsubscribe-Post", "Value": "List-Unsubscribe=One-Click"},
        ],
        Metadata=metadata or None,
    )
    return str(response.get("MessageID", ""))


def _canspam_footer_html(unsub_url: str) -> str:
    """CAN-SPAM footer: honest sender, physical address, one-click unsubscribe."""
    s = get_settings()
    return (
        "<hr style='margin-top:24px;border:0;border-top:1px solid #ddd'>"
        "<p style='color:#888;font-size:9pt;line-height:1.4'>"
        f"{s.company_name} · {s.company_postal_address}<br>"
        "You're receiving this because you're a MondayBrief client. "
        f"<a href='{unsub_url}'>Unsubscribe</a>."
        "</p>"
    )


def _canspam_footer_text(unsub_url: str) -> str:
    s = get_settings()
    return (
        f"\n\n—\n{s.company_name} · {s.company_postal_address}\n"
        f"Unsubscribe: {unsub_url}\n"
    )


def _fallback_html(bundle: BriefBundle) -> str:
    """Used when react-email export hasn't been run yet."""
    top = bundle.leads[0] if bundle.leads else None
    headline = (
        f"{len(bundle.leads)} new leads inside your service area this week"
        if bundle.leads else "No new high-fit leads this week"
    )
    top_html = ""
    if top:
        top_html = (
            f"<p><strong>Top lead:</strong> {top.name} — score {top.score}, "
            f"~${top.margin_est_monthly:,.0f}/mo if you win it.</p>"
        )
    return (
        f"<p>Hi {bundle.client_name} team,</p>"
        f"<p>{headline}.</p>"
        f"{top_html}"
        f"<p>Open the attached PDF for the full ranked list with owner names, phones, and ready-to-read openers.</p>"
        f"<p>— MondayBrief</p>"
    )


def _fallback_text(bundle: BriefBundle) -> str:
    return (
        f"Hi {bundle.client_name} team,\n\n"
        f"{len(bundle.leads)} new leads inside your service area this week. "
        f"The full ranked list is in the attached PDF.\n\n"
        f"— MondayBrief\n"
    )
