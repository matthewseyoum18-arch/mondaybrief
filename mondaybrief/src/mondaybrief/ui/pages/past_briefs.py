"""Past Briefs page — read-only history of pipeline_runs for this client.

Shows the last 20 delivered runs with sent_at, lead count, cost, and
delivery telemetry joined in from ``email_events`` (delivered / bounced /
opened). PDF download falls back to a local file read when ``pdf_path``
points at a filesystem path (the v1 default); if it's an HTTPS URL we
render a link instead.

We filter by ``client_uuid`` first (new FK), then ``client_id`` slug as a
backstop for runs written before the backfill.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from ...db import execute


_QUERY = """
SELECT
    pr.id,
    pr.week_of,
    pr.started_at,
    pr.finished_at,
    pr.status,
    pr.scored        AS lead_count,
    pr.cost_usd,
    pr.pdf_path,
    pr.postmark_delivery_id,
    COALESCE(SUM((ee.event_type = 'delivered')::int), 0)       AS delivered,
    COALESCE(SUM((ee.event_type = 'bounced')::int), 0)         AS bounced,
    COALESCE(SUM((ee.event_type = 'opened')::int), 0)          AS opened,
    COALESCE(SUM((ee.event_type = 'spam_complaint')::int), 0)  AS spam_complaints
FROM pipeline_runs pr
LEFT JOIN email_events ee
       ON ee.pipeline_run_id = pr.id
WHERE (pr.client_uuid = %(client_uuid)s OR pr.client_id = %(client_slug)s)
  AND pr.status IN ('shipped', 'delivered')
GROUP BY pr.id
ORDER BY pr.started_at DESC
LIMIT 20
"""


def _resolve_slug(client_id: str) -> str | None:
    rows = execute(
        "SELECT slug FROM clients WHERE id = %(id)s",
        {"id": client_id},
    )
    return rows[0][0] if rows else None


def _fetch_runs(client_id: str) -> list[dict]:
    slug = _resolve_slug(client_id) or ""
    rows = execute(_QUERY, {"client_uuid": client_id, "client_slug": slug})
    columns = [
        "id",
        "week_of",
        "started_at",
        "finished_at",
        "status",
        "lead_count",
        "cost_usd",
        "pdf_path",
        "postmark_delivery_id",
        "delivered",
        "bounced",
        "opened",
        "spam_complaints",
    ]
    return [dict(zip(columns, row)) for row in rows]


def _is_url(path: str | None) -> bool:
    if not path:
        return False
    return path.startswith("http://") or path.startswith("https://")


def render(client_id: str) -> None:
    st.title("Past Briefs")
    st.caption("Your last 20 weekly briefs — delivered, opened, and how much each one cost to produce.")

    try:
        runs = _fetch_runs(client_id)
    except Exception as exc:  # noqa: BLE001 - surface DB errors to operator
        st.error(f"Could not load past briefs: {exc}")
        return

    if not runs:
        st.info(
            "No briefs delivered yet. Your first Monday brief lands once the "
            "scheduler picks up your customer book — typically the Monday "
            "after onboarding."
        )
        return

    # Headline metric strip.
    total_leads = sum(r.get("lead_count") or 0 for r in runs)
    total_cost = sum(float(r.get("cost_usd") or 0) for r in runs)
    total_delivered = sum(r.get("delivered") or 0 for r in runs)
    col1, col2, col3 = st.columns(3)
    col1.metric("Briefs", len(runs))
    col2.metric("Leads delivered", total_leads)
    col3.metric("Total spend", f"${total_cost:,.2f}")

    st.divider()

    # Tabular view first, then per-row download below.
    table = [
        {
            "Sent": (r.get("finished_at") or r.get("started_at")),
            "Week of": r.get("week_of"),
            "Leads": r.get("lead_count") or 0,
            "Cost (USD)": float(r.get("cost_usd") or 0),
            "Delivered": r.get("delivered") or 0,
            "Opened": r.get("opened") or 0,
            "Bounced": r.get("bounced") or 0,
        }
        for r in runs
    ]
    st.dataframe(table, use_container_width=True, hide_index=True)

    st.subheader("Download PDFs")
    for run in runs:
        sent_at = run.get("finished_at") or run.get("started_at")
        week_of = run.get("week_of")
        leads = run.get("lead_count") or 0
        cost = float(run.get("cost_usd") or 0)
        pdf_path = run.get("pdf_path")

        with st.container(border=True):
            top, right = st.columns([4, 1])
            top.markdown(
                f"**Week of {week_of}** &nbsp;·&nbsp; "
                f"Sent {sent_at} &nbsp;·&nbsp; "
                f"{leads} leads &nbsp;·&nbsp; "
                f"${cost:,.2f}"
            )
            top.caption(
                f"Delivered: {run.get('delivered') or 0} &nbsp;·&nbsp; "
                f"Opened: {run.get('opened') or 0} &nbsp;·&nbsp; "
                f"Bounced: {run.get('bounced') or 0}"
            )

            if not pdf_path:
                right.caption("No PDF on file")
                continue

            if _is_url(pdf_path):
                right.link_button("Open PDF", pdf_path)
                continue

            # Local filesystem path — read bytes for a download button.
            p = Path(pdf_path)
            if not p.exists():
                right.caption("PDF not found")
                continue
            try:
                pdf_bytes = p.read_bytes()
            except Exception:  # noqa: BLE001 - filesystem errors
                right.caption("PDF unreadable")
                continue
            right.download_button(
                label="Download",
                data=pdf_bytes,
                file_name=p.name,
                mime="application/pdf",
                key=f"dl_{run['id']}",
            )
