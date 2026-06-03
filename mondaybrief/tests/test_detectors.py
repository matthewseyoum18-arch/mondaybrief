"""Unit tests for per-source signal detectors.

Each detector must emit the expected Signal(s) for a representative raw row and
``[]`` for non-matches. Pure — no network.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mondaybrief.models import RawLead  # noqa: E402
from mondaybrief.score import detectors as d  # noqa: E402


def _lead(source, **raw):
    return RawLead(
        source=source, source_id="x", name="Test Biz", address="1 Main St",
        city="Chicago", state="IL", date_issued=date(2026, 6, 1), raw_json=raw,
    )


def _types(signals):
    return [s.signal_type for s in signals]


# --- Chicago ---------------------------------------------------------------
def test_chicago_license_specific_code_is_strong():
    sigs = d.detect_chicago_license(_lead("r5kz-chrr", application_type="ISSUE", license_code="1475"))
    assert _types(sigs) == ["chi_license_issue"]


def test_chicago_license_generic_code_is_weak():
    sigs = d.detect_chicago_license(_lead("r5kz-chrr", application_type="ISSUE", license_code="1006"))
    assert _types(sigs) == ["chi_license_limited"]


def test_chicago_license_ignores_other_source():
    assert d.detect_chicago_license(_lead("ydr8-5enu")) == []


def test_chicago_permit_types():
    assert _types(d.detect_chicago_permit(_lead("ydr8-5enu", permit_type="PERMIT - NEW CONSTRUCTION"))) == ["chi_permit_newcon"]
    assert _types(d.detect_chicago_permit(_lead("ydr8-5enu", permit_type="PERMIT - RENOVATION/ALTERATION"))) == ["chi_permit_alteration"]
    assert _types(d.detect_chicago_permit(_lead("ydr8-5enu", permit_type="PERMIT - SIGNS"))) == ["chi_permit_sign"]
    demo = d.detect_chicago_permit(_lead("ydr8-5enu", permit_type="PERMIT - WRECKING/DEMOLITION"))
    assert demo[0].is_hard_disqualify


def test_chicago_food_inspection_license_type_is_opening():
    sigs = d.detect_chicago_food_inspection(_lead("4ijn-s7e5", inspection_type="License"))
    assert _types(sigs) == ["chi_food_license"]


def test_chicago_food_inspection_routine_is_silent():
    assert d.detect_chicago_food_inspection(_lead("4ijn-s7e5", inspection_type="Canvass")) == []


def test_chicago_food_inspection_legacy_liquor_shape():
    # No inspection_type field -> legacy liquor-shaped fixture row.
    sigs = d.detect_chicago_food_inspection(_lead("4ijn-s7e5", doing_business_as_name="Bar X"))
    assert _types(sigs) == ["chi_liquor_new"]


def test_chicago_food_inspection_blank_type_is_silent_not_liquor():
    # Present-but-blank inspection_type on a real food row is a routine
    # inspection -> emit nothing (must NOT fall through to the 0.80 liquor path).
    sigs = d.detect_chicago_food_inspection(_lead("4ijn-s7e5", inspection_type="", license_start_date="2026-01-01"))
    assert sigs == []


# --- NYC -------------------------------------------------------------------
def test_nyc_permit_types():
    assert _types(d.detect_nyc_permit(_lead("nyc:ipu4-2q9a", job_type="NB"))) == ["nyc_dob_nb"]
    assert _types(d.detect_nyc_permit(_lead("nyc:ipu4-2q9a", job_type="A2"))) == ["nyc_dob_alt"]
    assert d.detect_nyc_permit(_lead("nyc:ipu4-2q9a", job_type="DM"))[0].is_hard_disqualify


def test_nyc_license_premise_vs_unfiltered():
    assert _types(d.detect_nyc_license(_lead("nyc:ic3t-wcy2", license_category="Restaurant"))) == ["nyc_dcwp_premise"]
    assert _types(d.detect_nyc_license(_lead("nyc:ic3t-wcy2", license_category="Tow Truck Driver"))) == ["nyc_dcwp_unfiltered"]


# --- market / reputation ---------------------------------------------------
def test_market_rfp_routing():
    assert _types(d.detect_market_rfp(_lead("rfp_board:passport:OCP-1"))) == ["rfp_passport"]
    assert _types(d.detect_market_rfp(_lead("rfp_board:demandstar:1"))) == ["rfp_demandstar"]
    assert _types(d.detect_market_rfp(_lead("rfp_board:sam:1"))) == ["rfp_govt"]


def test_reputation_is_inert_zero_prior():
    sigs = d.detect_reputation(_lead("review_drop:yelp:1"))
    assert _types(sigs) == ["review_drop"]
    assert sigs[0].precision_prior == 0.0


# --- negatives + registry --------------------------------------------------
def test_negative_flags_revoked_status_is_hard():
    sigs = d.detect_negative_flags(_lead("r5kz-chrr", license_status="REV"))
    assert sigs and sigs[0].is_hard_disqualify


def test_negative_flags_soft_lawsuit():
    sigs = d.detect_negative_flags(_lead("r5kz-chrr", lawsuit=True))
    assert sigs and not sigs[0].is_hard_disqualify and sigs[0].woe < 0


def test_run_detectors_unknown_source_falls_back():
    sigs = d.run_detectors(_lead("some-unknown-source"))
    assert _types(sigs) == ["unknown"]


def test_run_detectors_hard_negative_no_unknown_padding():
    # A demolition-only lead emits the negative but NOT an unknown filler.
    sigs = d.run_detectors(_lead("ydr8-5enu", permit_type="PERMIT - WRECKING/DEMOLITION"))
    assert any(s.is_hard_disqualify for s in sigs)
    assert "unknown" not in _types(sigs)


def test_run_detectors_positive_present_no_padding():
    sigs = d.run_detectors(_lead("r5kz-chrr", application_type="ISSUE", license_code="1475"))
    assert _types(sigs) == ["chi_license_issue"]
