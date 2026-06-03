"""Scoring orchestrator — deterministic engine + LLM narrative.

This module used to call Claude to *produce* a 0-100 score. It no longer does.
The number is now computed deterministically and per-client by
:mod:`score.engine` against a :class:`score.profile.ClientProfile`. The LLM
(:mod:`score.narrative`) writes only the ``why`` + ``opener`` prose, and only
for the top-N leads that actually make the brief.

``score_many`` keeps its original signature so ``pipeline._score`` is a drop-in
caller; it gains an optional ``profile`` (seeded from the customer book when not
supplied) and ``as_of`` (for deterministic tests).
"""
from __future__ import annotations

from datetime import date

from ..config import get_settings
from ..models import Customer, EnrichedLead, ScoredLead
from . import engine, narrative
from .profile import ClientProfile, seed_from_book
from .signals import classify_signal


def _margin_uplift_pct(est_monthly: float, customers: list[Customer]) -> float:
    """How far above (or below) the cleaner's average account this lead's
    estimated margin sits, in percentage points. 0 when the book is empty."""
    revs = [float(c.monthly_rev) for c in customers if c.monthly_rev]
    if not revs:
        return 0.0
    avg = sum(revs) / len(revs)
    if avg <= 0:
        return 0.0
    return round((est_monthly - avg) / avg * 100.0, 1)


def _to_scored_lead(
    lead: EnrichedLead,
    result: engine.EngineResult,
    why: str,
    opener: str,
    uplift_pct: float,
) -> ScoredLead:
    comp = result.components
    return ScoredLead(
        name=lead.dba or lead.name,
        address=lead.address,
        category=result.category,
        score=result.score,
        tier=result.tier,
        margin_est_monthly=result.margin_est_monthly,
        margin_uplift_pct=uplift_pct,
        why=why,
        opener=opener,
        signal_class=result.signal_class,
        margin_score=comp.margin,
        route_score=comp.route,
        category_score=comp.category,
        timing_score=comp.timing,
        signal_score=comp.signal_class,
        signal_confidence=result.signal_confidence,
        corroboration_count=result.corroboration_count,
        signal_evidence=result.signal_evidence,
    )


def _score_with_fusion(
    leads: list[EnrichedLead],
    prof: ClientProfile,
    as_of: date,
    floor: float,
) -> list[tuple[engine.EngineResult, EnrichedLead]]:
    """v2 path: run detectors over every lead, FUSE signals per entity
    (name+address), collapse corroborated duplicates to one representative lead,
    drop entities that are disqualified or below the confidence floor, and score
    the survivor with its fused confidence.

    This is where the signal layer earns its keep: a business that appears in a
    license feed AND a permit feed becomes ONE lead with stacked confidence
    instead of two thin ones, and a margin-rich lead with only a weak signal is
    suppressed before it can buy its way onto the brief."""
    from .detectors import run_detectors
    from .signal_layer import Signal, entity_key_for, fuse

    per_lead: list[tuple[EnrichedLead, list[Signal]]] = [(l, run_detectors(l)) for l in leads]

    groups: dict[str, list[tuple[EnrichedLead, list[Signal]]]] = {}
    for lead, sigs in per_lead:
        groups.setdefault(entity_key_for(lead), []).append((lead, sigs))

    def _best_positive_woe(item: tuple[EnrichedLead, list[Signal]]) -> float:
        pos = [s.woe for s in item[1] if not s.is_negative]
        return max(pos) if pos else -999.0

    out: list[tuple[engine.EngineResult, EnrichedLead]] = []
    for members in groups.values():
        all_signals = [s for _, sigs in members for s in sigs]
        fusion = fuse(all_signals, as_of)
        if fusion.is_disqualified or fusion.p_fused < floor:
            continue
        rep_lead, _ = max(members, key=_best_positive_woe)
        out.append((engine.score_lead(rep_lead, prof, as_of=as_of, fusion=fusion), rep_lead))
    return out


def score_many(
    leads: list[EnrichedLead],
    customers: list[Customer],
    nearest_map: dict[str, int] | None = None,
    *,
    client_id: str | None = None,
    pipeline_run_id: int | None = None,
    profile: ClientProfile | None = None,
    as_of: date | None = None,
    top_n: int | None = None,
) -> tuple[list[ScoredLead], float]:
    """Score the week's surviving leads against one client profile.

    Returns ``(sorted_leads, total_llm_cost_usd)``. Disqualified leads are
    dropped before scoring. Every lead gets a deterministic engine score +
    component breakdown; only the top-N earn an LLM-written narrative (the rest
    get a zero-cost template, since they fall off the brief anyway).
    """
    prof = profile or seed_from_book(client_id or "default", customers)
    as_of = as_of or date.today()
    top_n = top_n if top_n is not None else get_settings().top_leads_per_brief

    customer_by_id = {c.id: c for c in customers if c.id is not None}

    settings = get_settings()
    if settings.signal_layer_enabled:
        engine_scored = _score_with_fusion(leads, prof, as_of, settings.confidence_floor)
    else:
        # Legacy path: one lead per row, coarse class, fixed per-class strength.
        scorable = [l for l in leads if classify_signal(l) != "disqualified"]
        engine_scored = [(engine.score_lead(l, prof, as_of=as_of), l) for l in scorable]
    engine_scored.sort(key=lambda t: -t[0].score)

    total_cost = 0.0
    out: list[ScoredLead] = []
    for i, (result, lead) in enumerate(engine_scored):
        nearest = customer_by_id.get(lead.nearest_customer_id)
        if i < top_n:
            nr = narrative.write_narrative(
                lead,
                score=result.score,
                tier=result.tier,
                category=result.category,
                signal_class=result.signal_class,
                margin_est_monthly=result.margin_est_monthly,
                drive_minutes=result.drive_minutes,
                customers=customers,
                nearest_customer=nearest,
                client_id=client_id,
                pipeline_run_id=pipeline_run_id,
            )
            why, opener = nr.narrative.why, nr.narrative.opener
            total_cost += nr.cost_usd
        else:
            tmpl = narrative.template_narrative(lead, nearest, result.tier)
            why, opener = tmpl.why, tmpl.opener

        uplift = _margin_uplift_pct(result.margin_est_monthly, customers)
        out.append(_to_scored_lead(lead, result, why, opener, uplift))

    return out, total_cost
