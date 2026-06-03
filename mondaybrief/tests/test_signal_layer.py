"""Unit tests for the v2 signal-fusion layer.

Pin the fusion math, decay curves, entity resolution, corroboration lift, and
contradiction handling. Pure — no network, no API key. Expected probabilities
are hand-computed from base_logit = ln(0.03/0.97) ≈ -3.4761.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mondaybrief.models import RawLead  # noqa: E402
from mondaybrief.score import economics, signal_layer as sl  # noqa: E402

AS_OF = date(2026, 6, 1)


def _lead(source="r5kz-chrr", *, name="Joe's Diner", address="100 W Madison St",
          city="Chicago", date_issued=date(2026, 6, 1), raw=None):
    return RawLead(
        source=source, source_id="t1", name=name, address=address,
        city=city, state="IL", date_issued=date_issued, raw_json=raw or {},
    )


# --- math primitives -------------------------------------------------------
def test_logit_sigmoid_roundtrip():
    for p in (0.05, 0.35, 0.5, 0.72, 0.95):
        assert abs(sl.sigmoid(sl.logit(p)) - p) < 1e-9


def test_woe_sign_and_clamp():
    # A signal at the base rate carries ~0 evidence; above it, positive.
    assert abs(sl.woe_for_precision(economics.BASE_RATE)) < 1e-6
    assert sl.woe_for_precision(0.72) > sl.woe_for_precision(0.35) > 0
    # Clamped to ±WOE_CLAMP at the extremes.
    assert sl.woe_for_precision(0.999999) <= economics.WOE_CLAMP + 1e-9
    assert sl.woe_for_precision(1e-9) >= -economics.WOE_CLAMP - 1e-9


# --- single-signal fusion returns the signal's own precision ---------------
def test_lone_strong_signal_returns_its_precision():
    sig = sl.make_signal("chi_license_issue", _lead())  # prior 0.72, coincident, fresh
    res = sl.fuse([sig], AS_OF)
    assert abs(res.p_fused - 0.72) < 0.01
    assert res.signal_class == "new_opening"
    assert res.corroboration_count == 1


def test_lone_weak_permit_stays_below_floor():
    # New-construction permit prior 0.35, and it is LEADING: a fresh one is too
    # early (low ramp), so it is far below the 0.40 floor on its own.
    sig = sl.make_signal("chi_permit_newcon", _lead(source="ydr8-5enu"))
    res = sl.fuse([sig], AS_OF)
    assert res.p_fused < economics.CONFIDENCE_FLOOR


def test_lone_food_license_clears_floor():
    sig = sl.make_signal("chi_food_license", _lead(source="4ijn-s7e5"))  # 0.88 coincident
    res = sl.fuse([sig], AS_OF)
    assert res.p_fused >= economics.CONFIDENCE_FLOOR


# --- corroboration ---------------------------------------------------------
def test_corroboration_lifts_above_either_alone():
    lic = sl.make_signal("chi_license_issue", _lead())                 # license family
    # Permit at its peak relevance (date_event = peak days before AS_OF).
    permit_lead = _lead(source="ydr8-5enu", date_issued=date(2026, 2, 1))  # 120d old = peak
    permit = sl.make_signal("chi_permit_newcon", permit_lead)         # construction family
    fused = sl.fuse([lic, permit], AS_OF)
    lone_lic = sl.fuse([lic], AS_OF)
    lone_permit = sl.fuse([permit], AS_OF)
    assert fused.p_fused > lone_lic.p_fused
    assert fused.p_fused > lone_permit.p_fused
    assert fused.p_fused > 0.9
    assert fused.corroboration_count == 2
    assert set(fused.families) == {"license", "construction"}


def test_same_family_does_not_double_count():
    # Two construction-family signals (new construction + sign permit) take the
    # MAX, not the sum — they trace to one project.
    p1 = sl.make_signal("chi_permit_newcon", _lead(source="ydr8-5enu", date_issued=date(2026, 2, 1)))
    p2 = sl.make_signal("chi_permit_sign", _lead(source="ydr8-5enu", date_issued=date(2026, 5, 1)))
    one = sl.fuse([p1], AS_OF)
    both = sl.fuse([p1, p2], AS_OF)
    # Corroboration count stays 1 (one family); confidence is max-of, not sum-of.
    assert both.corroboration_count == 1
    assert both.p_fused <= max(one.p_fused, sl.fuse([p2], AS_OF).p_fused) + 1e-6


# --- contradiction ---------------------------------------------------------
def test_hard_disqualify_vetoes_everything():
    lic = sl.make_signal("chi_license_issue", _lead())
    demo = sl.make_negative_signal("demolition", _lead(source="ydr8-5enu"))
    res = sl.fuse([lic, demo], AS_OF)
    assert res.p_fused == 0.0
    assert res.is_disqualified
    assert res.signal_class == "disqualified"


def test_soft_negative_damps_but_survives():
    lic = sl.make_signal("chi_license_issue", _lead())          # ~0.72 alone
    law = sl.make_negative_signal("lawsuit", _lead())           # -0.6 woe
    damped = sl.fuse([lic, law], AS_OF)
    clean = sl.fuse([lic], AS_OF)
    assert damped.p_fused < clean.p_fused
    assert not damped.is_disqualified


# --- decay -----------------------------------------------------------------
def test_coincident_decay_halves_at_half_life():
    fresh = sl.make_signal("chi_license_issue", _lead(date_issued=AS_OF))   # 0d old
    old = sl.make_signal("chi_license_issue", _lead(date_issued=date(2026, 5, 2)))  # 30d old
    assert abs(sl.decay_relevance(fresh, AS_OF) - 1.0) < 1e-9
    assert abs(sl.decay_relevance(old, AS_OF) - 0.5) < 0.05  # one half-life (30d)


def test_coincident_hard_expiry():
    very_old = sl.make_signal("chi_license_issue", _lead(date_issued=date(2026, 1, 1)))  # >120d
    assert sl.decay_relevance(very_old, AS_OF) == 0.0


def test_leading_ramps_then_decays():
    # New-construction permit: lead_time 120d. Too-early < peak ramps up;
    # at peak r≈1; long after peak it decays back down.
    early = sl.make_signal("chi_permit_newcon", _lead(source="ydr8-5enu", date_issued=date(2026, 5, 20)))  # ~12d old
    at_peak = sl.make_signal("chi_permit_newcon", _lead(source="ydr8-5enu", date_issued=date(2026, 2, 1)))  # 120d
    r_early = sl.decay_relevance(early, AS_OF)
    r_peak = sl.decay_relevance(at_peak, AS_OF)
    assert r_early < 0.2          # brand-new permit barely counts
    assert r_peak > 0.9           # peak relevance when the opening nears


# --- entity resolution (decision #5) --------------------------------------
def test_entity_key_matches_legal_vs_dba_suffix_and_address_abbrev():
    a = sl.resolve_entity_key("Joe's Pizza LLC", "100 W Madison St", "Chicago")
    b = sl.resolve_entity_key("Joe's Pizza", "100 West Madison Street", "chicago")
    assert a == b


def test_entity_key_separates_different_names_same_address():
    # The multi-tenant tower case: two businesses at one address must NOT fuse.
    a = sl.resolve_entity_key("Joe's Pizza", "1 World Trade Center", "New York")
    b = sl.resolve_entity_key("Bob's Law Office", "1 World Trade Center", "New York")
    assert a != b


# --- review-fixed defects (adversarial review 2026-06-02) ------------------
def test_degenerate_entity_key_does_not_collapse_distinct_leads():
    # Suffix-only name + blank address normalizes to empty; two different rows
    # must NOT share '||city' (which would silently drop one in fusion).
    a = _lead(source="4ijn-s7e5", name="LLC", address="").model_copy(update={"source_id": "A1"})
    b = _lead(source="4ijn-s7e5", name="The Inc", address="").model_copy(update={"source_id": "B2"})
    assert sl.entity_key_for(a) != sl.entity_key_for(b)


def test_review_drop_signal_is_inert_not_a_veto():
    rev = sl.make_signal("review_drop", _lead(source="review_drop:yelp:1"))  # prior 0.0
    assert rev.woe == 0.0  # positive signal never carries negative evidence
    lic = sl.make_signal("chi_license_issue", _lead())
    assert abs(sl.fuse([lic, rev], AS_OF).p_fused - sl.fuse([lic], AS_OF).p_fused) < 1e-9


def test_day0_leading_signal_can_still_corroborate():
    lic = sl.make_signal("chi_license_issue", _lead(date_issued=AS_OF))
    permit = sl.make_signal("chi_permit_newcon", _lead(source="ydr8-5enu", date_issued=AS_OF))  # day 0
    res = sl.fuse([lic, permit], AS_OF)
    assert res.corroboration_count == 2
    assert "construction" in res.families


def test_dominant_class_tiebreak_is_order_independent():
    newcon = sl.make_signal("chi_permit_newcon", _lead(source="ydr8-5enu", date_issued=date(2026, 2, 1)))   # new_opening 0.35
    alt = sl.make_signal("nyc_dob_alt", _lead(source="nyc:ipu4-2q9a", date_issued=date(2026, 2, 1)))        # expansion 0.35
    assert sl.fuse([newcon, alt], AS_OF).signal_class == sl.fuse([alt, newcon], AS_OF).signal_class == "new_opening"


# --- determinism + empty ---------------------------------------------------
def test_empty_fuse_is_base_rate():
    res = sl.fuse([], AS_OF)
    assert abs(res.p_fused - economics.BASE_RATE) < 1e-9
    assert res.corroboration_count == 0


def test_signal_component_is_ten_times_p():
    res = sl.fuse([sl.make_signal("chi_license_issue", _lead())], AS_OF)
    assert abs(res.signal_component - 10.0 * res.p_fused) < 1e-6
