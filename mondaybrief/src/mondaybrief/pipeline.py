"""End-to-end pipeline orchestrator.

Two entry points:

* ``run_for_client(client_id, *, offline, out_dir)`` — legacy slug-keyed
  in-memory path. Kept intact for the offline smoke test (no DB, no
  Stripe, no Postmark send). Uses ``CLIENT_PROFILES`` and a CSV-backed
  customer book.

* ``run(*, client_id, offline, out_dir)`` — DB-backed production path.
  Looks up the ``clients`` row by UUID, gates on Stripe subscription
  status, writes a ``pipeline_runs`` row, inserts every ``ScoredLead``
  into ``scored_leads``, renders + ships the PDF via Postmark, and
  rolls up cost via ``observability.cost.update_run_cost``. This is
  what ``inngest/client.py`` invokes.

Each step calls into a single repo-backed module so the pipeline is easy to
read and easy to swap.
"""
from __future__ import annotations
import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import get_settings
from .models import BriefBundle, Customer, EnrichedLead, RawLead, ScoredLead
from .ingest.socrata import pull_since, load_fixture
from .ingest import nyc_socrata
from .enrich.geocode import batch_forward
from .enrich.territory import cell_for, service_area_cells, inside_service_area
from .enrich.drivetime import annotate_drive_times
from .dedup.splink_match import drop_existing_customers
from .render.pdf import render_pdf
from .observability.cost import (
    geocodio_cost,
    mapbox_cost,
    twilio_cost,
    update_run_cost,
)
from .db import connect, execute, insert_returning_id
from .billing.stripe_client import get_subscription_status
from .send.brief import send_brief

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"

CLIENT_PROFILES = {
    "ek":        {"name": "E&K Commercial Cleaning", "metro": "Chicago, IL",      "tz": "CT", "customer_csv": FIXTURES_DIR / "ek_customers.csv"},
    "teamclean": {"name": "Team Clean Inc.",          "metro": "Philadelphia, PA", "tz": "ET", "customer_csv": FIXTURES_DIR / "ek_customers.csv"},
    "sanitrol":  {"name": "Sanitrol",                 "metro": "Boston, MA",       "tz": "ET", "customer_csv": FIXTURES_DIR / "ek_customers.csv"},
}


@dataclass
class RunTelemetry:
    permits_pulled: int = 0
    geocoded: int = 0
    inside_area: int = 0
    after_dedup: int = 0
    scored: int = 0
    pdf_path: Optional[Path] = None
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass
class PipelineRunLease:
    run_id: int
    acquired: bool
    status: str
    pdf_path: Optional[str] = None
    postmark_delivery_id: Optional[str] = None
    cost_usd: Optional[float] = None


def _looks_like_uuid(value: str) -> bool:
    """Cheap UUID-shape check. We don't need strict RFC compliance — just
    enough to disambiguate from CLIENT_PROFILES slugs ('ek', 'teamclean')."""
    if not value or len(value) != 36:
        return False
    parts = value.split("-")
    return len(parts) == 5 and [len(p) for p in parts] == [8, 4, 4, 4, 12]


def _load_customers(
    client_or_profile: str | dict,
    *,
    client_uuid: Optional[str] = None,
) -> list[Customer]:
    """Load the cleaner's customer book.

    Three calling shapes:

    1. ``_load_customers("ek")`` — legacy slug. Reads CLIENT_PROFILES
       and the bundled CSV fixture.
    2. ``_load_customers({"customer_csv": Path(...)})`` — explicit
       profile dict with a CSV path (offline/back-compat).
    3. ``_load_customers({...}, client_uuid="...")`` — online mode.
       Profile dict has ``customer_csv = None``; rows come from the
       ``customers`` table filtered by ``client_uuid``.
    """
    # Resolve to a profile dict if we were handed a slug.
    if isinstance(client_or_profile, str):
        profile = CLIENT_PROFILES[client_or_profile]
    else:
        profile = client_or_profile

    csv_path: Optional[Path] = profile.get("customer_csv") if isinstance(profile, dict) else None

    customers: list[Customer] = []

    if csv_path:
        with Path(csv_path).open(encoding="utf-8") as fp:
            for row in csv.DictReader(fp):
                row["lat"] = float(row["lat"]) if row.get("lat") else None
                row["lng"] = float(row["lng"]) if row.get("lng") else None
                row["sqft"] = int(row["sqft"]) if row.get("sqft") else None
                row["monthly_rev"] = float(row["monthly_rev"]) if row.get("monthly_rev") else None
                customers.append(Customer(**row))
    elif client_uuid:
        # Online path — pull from Postgres by client_uuid.
        rows = execute(
            """
            SELECT id, client_id, name, address, city, state,
                   lat, lng, h3_cell, category, sqft, monthly_rev, status
              FROM customers
             WHERE client_uuid = %s
            """,
            (client_uuid,),
        )
        for r in rows:
            customers.append(
                Customer(
                    id=r[0],
                    client_id=r[1],
                    name=r[2],
                    address=r[3],
                    city=r[4],
                    state=r[5],
                    lat=float(r[6]) if r[6] is not None else None,
                    lng=float(r[7]) if r[7] is not None else None,
                    h3_cell=r[8],
                    category=r[9],
                    sqft=int(r[10]) if r[10] is not None else None,
                    monthly_rev=float(r[11]) if r[11] is not None else None,
                    status=r[12] or "active",
                )
            )

    # Tag each customer with an H3 cell if we have lat/lng but no cell yet.
    for c in customers:
        if c.lat is not None and c.lng is not None and not c.h3_cell:
            c.h3_cell = cell_for(c.lat, c.lng)
    return customers


_METRO_INGEST = {
    "chicago": pull_since,
    "nyc":     nyc_socrata.pull_since,
}

_METRO_FIXTURE = {
    "chicago": FIXTURES_DIR / "sample_permits.json",
    "nyc":     FIXTURES_DIR / "nyc_sample_permits.json",
}


def _pull_permits(
    *,
    offline: bool,
    metros: Optional[list[str]] = None,
) -> list[RawLead]:
    """Fan ingest across metros listed on the client's row.

    Defaults to Chicago when ``metros`` is empty/None so the legacy
    offline smoke test keeps working unchanged. Each metro is pulled
    independently and unioned; downstream H3 + Splink + scoring are
    metro-agnostic.
    """
    if not metros:
        metros = ["chicago"]
    metros = [m.lower().strip() for m in metros if m and m.strip()]

    all_leads: list[RawLead] = []
    for metro in metros:
        if offline:
            fixture_path = _METRO_FIXTURE.get(metro)
            if fixture_path and fixture_path.exists():
                all_leads.extend(load_fixture(fixture_path))
            continue
        ingest_fn = _METRO_INGEST.get(metro)
        if ingest_fn is None:
            # Unknown metro — skip rather than crash so a typo on a
            # clients.metros[] entry doesn't kill the run.
            continue
        all_leads.extend(ingest_fn())
    return all_leads


def _geocode_leads(leads: list[RawLead], *, offline: bool) -> list[EnrichedLead]:
    addresses = [f"{l.address}, {l.city}, {l.state}" for l in leads]
    coords = batch_forward(addresses, offline=offline)
    out: list[EnrichedLead] = []
    for lead, (lat, lng) in zip(leads, coords):
        enriched = EnrichedLead(**lead.model_dump(), lat=lat, lng=lng)
        if lat is not None and lng is not None:
            enriched.h3_cell = cell_for(lat, lng)
        out.append(enriched)
    return out


def _filter_to_service_area(
    leads: list[EnrichedLead], customers: list[Customer]
) -> list[EnrichedLead]:
    customer_cells = [c.h3_cell for c in customers if c.h3_cell]
    cells = service_area_cells(customer_cells, k_ring=4)
    return [l for l in leads if l.h3_cell and inside_service_area(l.h3_cell, cells)]


def _load_feedback_rows(client_uuid: str, lookback_days: int = 28) -> list[tuple[str, str]]:
    """Recent (category, thumbs) feedback for shrinkage tuning. Returns ``[]``
    on any DB error so scoring never breaks on an observability read."""
    try:
        rows = execute(
            """
            SELECT sl.category, lf.thumbs
              FROM lead_feedback lf
              JOIN scored_leads sl ON sl.id = lf.scored_lead_id
             WHERE lf.client_id = %s
               AND lf.created_at > NOW() - make_interval(days => %s)
             ORDER BY lf.created_at DESC
             LIMIT 200
            """,
            (client_uuid, lookback_days),
        )
    except Exception:
        return []
    return [(r[0] or "other", r[1]) for r in rows]


def _build_profile(
    client_id: str,
    customers: list[Customer],
    *,
    client_uuid: str | None = None,
    offline: bool = False,
):
    """Seed a per-client scoring profile from the customer book, then tune its
    category preferences from recent thumbs feedback when a DB is reachable.
    Offline / no-UUID runs use the seeded profile as-is (still per-client — it
    reflects this cleaner's book mix, contract floor, and drive radius)."""
    from .score.profile import seed_from_book, tune_from_feedback

    profile = seed_from_book(client_id, customers)
    if offline or not client_uuid:
        return profile
    rows = _load_feedback_rows(client_uuid)
    return tune_from_feedback(profile, rows) if rows else profile


def _score(
    leads: list[EnrichedLead],
    customers: list[Customer],
    *,
    client_id: str | None = None,
    pipeline_run_id: int | None = None,
    profile=None,
) -> tuple[list[ScoredLead], float]:
    """Deterministic per-client scoring (:mod:`score.engine`) + LLM narrative for
    the top-N (:mod:`score.narrative`).

    The engine needs no API key, so offline runs get real per-client scores;
    only the narrative falls back to a deterministic template when
    ``ANTHROPIC_API_KEY`` is absent. Always returns ``(sorted_leads, cost_usd)``.
    """
    from .score.claude_score import score_many
    return score_many(
        leads,
        customers,
        client_id=client_id,
        pipeline_run_id=pipeline_run_id,
        profile=profile,
    )


def run_for_client(
    client_id: str,
    *,
    offline: bool = False,
    out_dir: Path | str | None = None,
) -> tuple[BriefBundle, RunTelemetry]:
    """End-to-end run. Returns the bundle and a telemetry struct."""
    profile = CLIENT_PROFILES[client_id]
    telemetry = RunTelemetry()
    settings = get_settings()

    customers = _load_customers(client_id)
    raw_leads = _pull_permits(offline=offline)
    telemetry.permits_pulled = len(raw_leads)

    enriched = _geocode_leads(raw_leads, offline=offline)
    telemetry.geocoded = sum(1 for l in enriched if l.lat is not None)

    in_area = _filter_to_service_area(enriched, customers)
    telemetry.inside_area = len(in_area)

    survivors, dedup_stats = drop_existing_customers(customers, in_area)
    telemetry.after_dedup = dedup_stats["out"]

    annotate_drive_times(survivors, customers)
    score_profile = _build_profile(client_id, customers, offline=offline)
    scored, _llm_cost = _score(survivors, customers, profile=score_profile)
    telemetry.scored = len(scored)
    top = scored[: settings.top_leads_per_brief]

    bundle = BriefBundle(
        client_id=client_id,
        client_name=profile["name"],
        metro=profile["metro"],
        week_of=date.today(),
        generated_at=datetime.now(timezone.utc),
        leads=top,
        customer_count=len(customers),
        permits_pulled=telemetry.permits_pulled,
        leads_inside_area=telemetry.inside_area,
        leads_after_dedup=telemetry.after_dedup,
    )

    out_dir = Path(out_dir) if out_dir else Path("out")
    pdf_path = out_dir / f"brief_{client_id}_{date.today().isoformat()}.pdf"
    telemetry.pdf_path = render_pdf(bundle, pdf_path)

    return bundle, telemetry


# ---------------------------------------------------------------------------
# Online (DB-backed) entry point — called by inngest/client.py
# ---------------------------------------------------------------------------


def _load_client_row(client_uuid: str) -> Optional[dict]:
    """Fetch a single ``clients`` row by UUID. Returns None if missing."""
    rows = execute(
        """
        SELECT id, slug, name, contact_email, postmark_stream, metros,
               stripe_customer_id, active
          FROM clients
         WHERE id = %s
        """,
        (client_uuid,),
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "id": str(r[0]),
        "slug": r[1],
        "name": r[2],
        "contact_email": r[3],
        "postmark_stream": r[4],
        "metros": list(r[5]) if r[5] is not None else [],
        "stripe_customer_id": r[6],
        "active": bool(r[7]),
    }


def _profile_from_client_row(row: dict) -> dict:
    """Build a CLIENT_PROFILES-shaped dict from a ``clients`` row.

    Metro/timezone defaults come from the v1 cleaners-only assumption
    (Chicago). When a client lists explicit metros in the array we use
    the first one as a display hint.
    """
    metros = row.get("metros") or []
    if metros and isinstance(metros, list) and metros[0]:
        # Capitalize 'chicago' -> 'Chicago, IL'. We don't ship per-metro
        # state lookup yet; CLIENT_PROFILES carries the IL anchor.
        metro = "Chicago, IL" if metros[0].lower() == "chicago" else metros[0]
    else:
        metro = "Chicago, IL"
    return {
        "name": row["name"],
        "metro": metro,
        "tz": "CT",
        "customer_csv": None,  # online — customers come from DB
    }


def _can_run(client_uuid: str, client_row: dict) -> bool:
    """Gate brief delivery on subscription state.

    Allow when:
      * subscription.status in {'active', 'trialing'}, OR
      * clients.active = true AND no subscription row exists yet
        (grace period for unbilled pilots that we onboarded by hand).
    """
    sub = get_subscription_status(client_uuid)
    if sub.get("status") in {"active", "trialing"}:
        return True
    if sub.get("status") == "none" and client_row.get("active"):
        return True
    return False


def _acquire_pipeline_run_start(
    *,
    client_slug: str,
    client_uuid: str,
    week_of: date,
) -> PipelineRunLease:
    """Acquire the one send lease for ``client_uuid``/``week_of``.

    The matching partial unique index in ``schema.sql`` is the real lock:
    only one ``running`` or ``shipped`` run can exist for a client/week.
    Failed pre-send runs are retryable because they fall out of the
    partial index. A concurrent delivery or retry receives the existing
    run instead of creating a second email side effect.
    """
    with connect() as conn:
        row = conn.execute(
            """
            INSERT INTO pipeline_runs (client_id, client_uuid, week_of, status, started_at)
            VALUES (%s, %s, %s, 'running', NOW())
            ON CONFLICT (client_uuid, week_of)
              WHERE status IN ('running', 'shipped')
              DO NOTHING
            RETURNING id, status, pdf_path, postmark_delivery_id, cost_usd
            """,
            (client_slug, client_uuid, week_of),
        ).fetchone()
        if row:
            return PipelineRunLease(
                run_id=int(row[0]),
                acquired=True,
                status=row[1],
                pdf_path=row[2],
                postmark_delivery_id=row[3],
                cost_usd=float(row[4]) if row[4] is not None else None,
            )

        row = conn.execute(
            """
            SELECT id, status, pdf_path, postmark_delivery_id, cost_usd
              FROM pipeline_runs
             WHERE client_uuid = %s
               AND week_of = %s
               AND status IN ('running', 'shipped')
             ORDER BY started_at DESC, id DESC
             LIMIT 1
            """,
            (client_uuid, week_of),
        ).fetchone()
        if not row:
            raise RuntimeError(
                "pipeline run lease conflict was detected, but no active run was found"
            )
        return PipelineRunLease(
            run_id=int(row[0]),
            acquired=False,
            status=row[1],
            pdf_path=row[2],
            postmark_delivery_id=row[3],
            cost_usd=float(row[4]) if row[4] is not None else None,
        )


def _update_pipeline_run_finish(
    run_id: int,
    *,
    status: str,
    telemetry: RunTelemetry,
    pdf_path: Optional[Path],
    postmark_delivery_id: Optional[str],
    cost_usd: Optional[float],
    error: Optional[str] = None,
) -> None:
    """Single UPDATE that closes out a run row. Idempotent — safe to call
    from both the happy path and the except block."""
    with connect() as conn:
        conn.execute(
            """
            UPDATE pipeline_runs
               SET status               = %s,
                   finished_at          = NOW(),
                   permits_pulled       = %s,
                   geocoded             = %s,
                   inside_area          = %s,
                   after_dedup          = %s,
                   scored               = %s,
                   pdf_path             = %s,
                   postmark_delivery_id = %s,
                   cost_usd             = %s,
                   error                = %s
             WHERE id = %s
               AND NOT (status = 'shipped' AND %s = 'failed')
            """,
            (
                status,
                telemetry.permits_pulled,
                telemetry.geocoded,
                telemetry.inside_area,
                telemetry.after_dedup,
                telemetry.scored,
                str(pdf_path) if pdf_path else None,
                postmark_delivery_id,
                round(cost_usd, 4) if cost_usd is not None else None,
                error,
                run_id,
                status,
            ),
        )


def _insert_scored_lead(
    *,
    client_slug: str,
    client_uuid: str,
    week_of: date,
    lead: ScoredLead,
    enriched: Optional[EnrichedLead] = None,
) -> int:
    """INSERT one ``scored_leads`` row and return its id.

    The UNIQUE(client_id, week_of, address) constraint means a re-run in
    the same week will collide. We ON CONFLICT DO UPDATE so the run is
    idempotent — the latest score/why/opener overwrites.
    """
    lat = enriched.lat if enriched else None
    lng = enriched.lng if enriched else None
    h3_cell = enriched.h3_cell if enriched else None
    owner_name = enriched.owner_name if enriched else None
    owner_phone = enriched.owner_phone if enriched else None
    owner_phone_valid = enriched.owner_phone_valid if enriched else None
    drive_minutes = enriched.drive_minutes if enriched else None
    nearest_customer_id = enriched.nearest_customer_id if enriched else None

    return insert_returning_id(
        """
        INSERT INTO scored_leads (
            client_id, client_uuid, week_of,
            name, address, lat, lng, h3_cell, category,
            owner_name, owner_phone, owner_phone_valid,
            nearest_customer_id, drive_minutes,
            margin_est_monthly, margin_uplift_pct,
            score, tier, why, opener,
            margin_score, route_score, category_score, timing_score, signal_score,
            sources, status
        ) VALUES (
            %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s::jsonb, 'queued'
        )
        ON CONFLICT (client_id, week_of, address) DO UPDATE
          SET name               = EXCLUDED.name,
              category           = EXCLUDED.category,
              score              = EXCLUDED.score,
              tier               = EXCLUDED.tier,
              why                = EXCLUDED.why,
              opener             = EXCLUDED.opener,
              margin_est_monthly = EXCLUDED.margin_est_monthly,
              margin_uplift_pct  = EXCLUDED.margin_uplift_pct,
              margin_score       = EXCLUDED.margin_score,
              route_score        = EXCLUDED.route_score,
              category_score     = EXCLUDED.category_score,
              timing_score       = EXCLUDED.timing_score,
              signal_score       = EXCLUDED.signal_score,
              client_uuid        = EXCLUDED.client_uuid
        RETURNING id
        """,
        (
            client_slug, client_uuid, week_of,
            lead.name, lead.address, lat, lng, h3_cell, lead.category,
            owner_name, owner_phone, owner_phone_valid,
            nearest_customer_id, drive_minutes,
            lead.margin_est_monthly, lead.margin_uplift_pct,
            lead.score, lead.tier, lead.why, lead.opener,
            lead.margin_score, lead.route_score, lead.category_score,
            lead.timing_score, lead.signal_score,
            json.dumps({}),
        ),
    )


def run(
    *,
    client_id: str,
    offline: bool = False,
    out_dir: Path | str | None = None,
) -> dict:
    """Online (DB-backed) pipeline entry point.

    ``client_id`` is normally a UUID string referencing ``clients.id``.
    For back-compat with the offline smoke test we fall through to
    :func:`run_for_client` whenever the value looks like a slug (or
    ``offline=True`` and the value is in ``CLIENT_PROFILES``).
    """
    # ---- back-compat / offline fast path -------------------------------
    if offline or not _looks_like_uuid(client_id):
        if client_id in CLIENT_PROFILES:
            bundle, telemetry = run_for_client(client_id, offline=offline, out_dir=out_dir)
            return {
                "client_id": client_id,
                "run_id": None,
                "leads": [l.model_dump() for l in bundle.leads],
                "pdf_path": str(telemetry.pdf_path) if telemetry.pdf_path else None,
                "cost_usd": telemetry.cost_usd,
                "postmark_message_id": None,
                "status": "shipped",
            }
        raise ValueError(
            f"run(client_id={client_id!r}): not a UUID and not in CLIENT_PROFILES"
        )

    # ---- online path ---------------------------------------------------
    client_row = _load_client_row(client_id)
    if client_row is None:
        raise ValueError(f"run(client_id={client_id!r}): no clients row with that id")

    client_uuid = client_row["id"]
    client_slug = client_row["slug"]

    if not _can_run(client_uuid, client_row):
        return {
            "client_id": client_id,
            "run_id": None,
            "leads": [],
            "pdf_path": None,
            "cost_usd": 0.0,
            "postmark_message_id": None,
            "status": "skipped",
            "skipped": "no_active_subscription",
        }

    settings = get_settings()
    profile = _profile_from_client_row(client_row)
    week_of = date.today()
    telemetry = RunTelemetry()
    lease = _acquire_pipeline_run_start(
        client_slug=client_slug,
        client_uuid=client_uuid,
        week_of=week_of,
    )
    run_id = lease.run_id
    if not lease.acquired:
        skipped = "already_shipped" if lease.status == "shipped" else "run_in_progress"
        return {
            "client_id": client_id,
            "run_id": run_id,
            "leads": [],
            "pdf_path": lease.pdf_path,
            "cost_usd": lease.cost_usd or 0.0,
            "postmark_message_id": lease.postmark_delivery_id,
            "status": lease.status if lease.status == "shipped" else "skipped",
            "skipped": skipped,
        }

    pdf_path: Optional[Path] = None
    postmark_message_id: Optional[str] = None
    total_cost: float = 0.0
    final_status = "running"

    try:
        # ---- load + ingest --------------------------------------------
        customers = _load_customers(profile, client_uuid=client_uuid)
        raw_leads = _pull_permits(offline=offline, metros=client_row.get("metros") or ["chicago"])
        telemetry.permits_pulled = len(raw_leads)

        enriched = _geocode_leads(raw_leads, offline=offline)
        telemetry.geocoded = sum(1 for l in enriched if l.lat is not None)

        in_area = _filter_to_service_area(enriched, customers)
        telemetry.inside_area = len(in_area)

        survivors, dedup_stats = drop_existing_customers(customers, in_area)
        telemetry.after_dedup = dedup_stats["out"]

        annotate_drive_times(survivors, customers)
        score_profile = _build_profile(
            client_slug, customers, client_uuid=client_uuid, offline=offline
        )

        # ---- score (with run + client trace tags) ---------------------
        scored, llm_cost = _score(
            survivors,
            customers,
            client_id=client_uuid,
            pipeline_run_id=run_id,
            profile=score_profile,
        )
        telemetry.scored = len(scored)
        top = scored[: settings.top_leads_per_brief]

        # Map ScoredLead.address -> EnrichedLead so we can persist
        # geo/owner fields alongside the score. Survivors share the
        # same address keying since dedup never rewrites address.
        enriched_by_addr = {e.address: e for e in survivors}

        # ---- persist scored leads in display order --------------------
        scored_lead_ids: list[int] = []
        for s in top:
            sid = _insert_scored_lead(
                client_slug=client_slug,
                client_uuid=client_uuid,
                week_of=week_of,
                lead=s,
                enriched=enriched_by_addr.get(s.address),
            )
            scored_lead_ids.append(sid)

        # ---- build bundle --------------------------------------------
        bundle = BriefBundle(
            client_id=client_slug,
            client_name=client_row["name"],
            metro=profile["metro"],
            week_of=week_of,
            generated_at=datetime.now(timezone.utc),
            leads=top,
            customer_count=len(customers),
            permits_pulled=telemetry.permits_pulled,
            leads_inside_area=telemetry.inside_area,
            leads_after_dedup=telemetry.after_dedup,
        )

        # ---- render PDF (feedback tokens signed with client UUID) ----
        out_dir_path = Path(out_dir) if out_dir else Path("out")
        pdf_path = out_dir_path / f"brief_{client_slug}_{week_of.isoformat()}.pdf"
        pdf_path = render_pdf(
            bundle,
            pdf_path,
            scored_lead_ids=scored_lead_ids,
            client_uuid=client_uuid,
        )
        telemetry.pdf_path = pdf_path

        # ---- send via Postmark ---------------------------------------
        postmark_message_id = send_brief(
            bundle,
            pdf_path,
            to_email=client_row["contact_email"],
            client_id=client_uuid,
            pipeline_run_id=run_id,
        )
        final_status = "shipped"
        _update_pipeline_run_finish(
            run_id,
            status=final_status,
            telemetry=telemetry,
            pdf_path=pdf_path,
            postmark_delivery_id=postmark_message_id,
            cost_usd=total_cost,
        )

        # ---- cost rollup ---------------------------------------------
        total_cost = update_run_cost(
            run_id,
            llm_cost_usd=llm_cost,
            geocoding_cost_usd=geocodio_cost(telemetry.geocoded),
            mapbox_cost_usd=mapbox_cost(0),
            twilio_cost_usd=twilio_cost(0),
        ) or 0.0
        telemetry.cost_usd = total_cost

    except BaseException as exc:
        if postmark_message_id is not None:
            final_status = "shipped"
            telemetry.errors.append(repr(exc))
            _update_pipeline_run_finish(
                run_id,
                status="shipped",
                telemetry=telemetry,
                pdf_path=pdf_path,
                postmark_delivery_id=postmark_message_id,
                cost_usd=total_cost,
                error=f"post_send_error: {repr(exc)}",
            )
        else:
            final_status = "failed"
            # Always close out the run row before propagating so the dashboard
            # never sees a stuck 'running' state.
            try:
                _update_pipeline_run_finish(
                    run_id,
                    status="failed",
                    telemetry=telemetry,
                    pdf_path=pdf_path,
                    postmark_delivery_id=postmark_message_id,
                    cost_usd=total_cost,
                    error=repr(exc),
                )
            finally:
                raise
    else:
        # Refresh the shipped run row with the final totals after cost rollup.
        _update_pipeline_run_finish(
            run_id,
            status=final_status,
            telemetry=telemetry,
            pdf_path=pdf_path,
            postmark_delivery_id=postmark_message_id,
            cost_usd=total_cost,
        )

    return {
        "client_id": client_id,
        "run_id": run_id,
        "leads": [l.model_dump() for l in bundle.leads],
        "pdf_path": str(pdf_path) if pdf_path else None,
        "cost_usd": total_cost,
        "postmark_message_id": postmark_message_id,
        "status": final_status,
    }
