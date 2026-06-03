"""Unit tests for the deterministic scoring engine.

These pin the component math, tier boundaries, risk penalties, and determinism.
No network, no API key — the engine is pure.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mondaybrief.models import EnrichedLead  # noqa: E402
from mondaybrief.score import economics, engine  # noqa: E402
from mondaybrief.score.profile import ClientProfile  # noqa: E402

AS_OF = date(2026, 6, 1)


def _lead(category="office", *, drive=6.0, days_old=4, sqft=None, source="r5kz-chrr", raw=None):
    rj = {"license_description": category}
    if sqft:
        rj["sqft"] = sqft
    if raw:
        rj.update(raw)
    return EnrichedLead(
        source=source,
        source_id="t1",
        name=f"{category} biz",
        address="100 W Madison St",
        city="Chicago",
        state="IL",
        date_issued=date(2026, 6, 1) if days_old == 0 else date(2026, 5, 28),
        raw_json=rj,
        drive_minutes=drive,
    )


def _profile(**kw):
    return ClientProfile(client_id="t", **kw)


# --- component scorers -----------------------------------------------------
def test_route_score_thresholds():
    assert engine.route_score(3) == 10.0
    assert engine.route_score(8) == 8.0
    assert engine.route_score(25) == 5.0
    assert engine.route_score(90) == 0.5
    # Unknown drive time is neutral, not punished.
    assert engine.route_score(None) == 5.0


def test_timing_decay_halflife():
    # Fresh today -> 10; one half-life (21d) ago -> ~5.
    assert engine.timing_score(AS_OF, AS_OF) == 10.0
    half = engine.timing_score(date(2026, 5, 11), AS_OF)  # 21 days old
    assert 4.8 <= half <= 5.2
    assert engine.timing_score(None, AS_OF) == 5.0


def test_margin_score_bands_use_net_margin():
    # Medical (premium $psf) outscores generic retail on margin.
    med, med_est = engine.margin_score("medical office", 9000)
    ret, ret_est = engine.margin_score("retail", 1200)
    assert med > ret
    assert med_est > ret_est


def test_signal_class_strength_ordering():
    assert (
        engine.signal_class_score("new_opening")
        > engine.signal_class_score("churn_intent")
        > engine.signal_class_score("expansion")
        > engine.signal_class_score("unknown")
    )


# --- combine + tier --------------------------------------------------------
def test_combine_normalizes_by_weight_sum():
    comp = engine.ComponentScores(margin=10, route=10, category=10, timing=10, signal_class=10)
    # All-tens -> 1.0 regardless of weight magnitudes.
    assert engine.combine(comp, {"margin": 2, "route": 2, "category": 2, "timing": 2, "signal_class": 2}) == 1.0
    zero = engine.ComponentScores(0, 0, 0, 0, 0)
    assert engine.combine(zero, economics.GLOBAL_DEFAULT_WEIGHTS) == 0.0


def test_tier_boundaries():
    assert engine.to_tier(70) == "A"
    assert engine.to_tier(69) == "B"
    assert engine.to_tier(45) == "B"
    assert engine.to_tier(44) == "C"
    assert engine.to_tier(30) == "C"
    assert engine.to_tier(29) == "drop"


def test_missing_weight_key_falls_back_to_global():
    comp = engine.ComponentScores(margin=10, route=0, category=0, timing=0, signal_class=0)
    # Only margin weight supplied; others fall back to global defaults (non-zero
    # denominator) so the score isn't silently inflated to margin-only.
    raw = engine.combine(comp, {"margin": 1.0})
    assert 0.0 < raw < 1.0


# --- risk penalties --------------------------------------------------------
def test_excluded_category_penalty_lowers_score():
    lead = _lead("restaurant", drive=4, sqft=4000)
    base = engine.score_lead(lead, _profile(), as_of=AS_OF)
    excl = engine.score_lead(lead, _profile(exclusions=["restaurant"]), as_of=AS_OF)
    assert excl.score < base.score


def test_contract_floor_only_when_set():
    # Tiny cafe: no floor -> some score; explicit high floor -> penalized.
    lead = _lead("cafe", drive=5, sqft=1200)
    no_floor = engine.score_lead(lead, _profile(min_contract_monthly=0), as_of=AS_OF)
    with_floor = engine.score_lead(lead, _profile(min_contract_monthly=5000), as_of=AS_OF)
    assert with_floor.score < no_floor.score


def test_union_excluded_penalty():
    lead = _lead("office", drive=5, raw={"union": True})
    base = engine.score_lead(lead, _profile(), as_of=AS_OF)
    excl = engine.score_lead(lead, _profile(exclusions=["union"]), as_of=AS_OF)
    assert excl.score < base.score


# --- determinism + shape ---------------------------------------------------
def test_score_is_deterministic():
    lead = _lead("dental clinic", drive=7, sqft=3000)
    a = engine.score_lead(lead, _profile(), as_of=AS_OF)
    b = engine.score_lead(lead, _profile(), as_of=AS_OF)
    assert a.score == b.score
    assert 0 <= a.score <= 100
    assert a.tier in {"A", "B", "C", "drop"}
    assert set(a.components.as_dict()) == {"margin", "route", "category", "timing", "signal_class"}


def test_disqualified_signal_resolves():
    # Demolition permit classifies as disqualified -> signal strength 0.
    lead = _lead("office", source="ydr8-5enu", raw={"permit_type": "PERMIT - WRECKING/DEMOLITION"})
    res = engine.score_lead(lead, _profile(), as_of=AS_OF)
    assert res.signal_class == "disqualified"
    assert res.components.signal_class == 0.0
