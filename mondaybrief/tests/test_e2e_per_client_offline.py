"""Offline end-to-end proof that briefs are tailored per client.

Runs the SAME real fixture lead-set (fixtures/sample_permits.json) through the
real scoring stack — load_fixture -> offline geocode -> H3 -> drive-time ->
score_many — against two contrasting customer books, and asserts the resulting
briefs rank differently and in the right direction. ``top_n=0`` skips the LLM
narrative entirely, so the test is fully deterministic and needs no API key or
database.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mondaybrief.enrich.geocode import batch_forward  # noqa: E402
from mondaybrief.enrich.territory import cell_for  # noqa: E402
from mondaybrief.enrich.drivetime import annotate_drive_times  # noqa: E402
from mondaybrief.ingest.socrata import load_fixture  # noqa: E402
from mondaybrief.models import Customer, EnrichedLead  # noqa: E402
from mondaybrief.score.claude_score import score_many  # noqa: E402
from mondaybrief.score.profile import seed_from_book  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_permits.json"
AS_OF = date(2026, 6, 1)


def _customer(name, cat, lat, lng, rev):
    c = Customer(client_id="x", name=name, address=name, city="Chicago", state="IL",
                 lat=lat, lng=lng, category=cat, monthly_rev=rev)
    c.h3_cell = cell_for(lat, lng)
    return c


# Medical/office-heavy cleaner (mirrors the ek book).
MED_BOOK = [
    _customer("LP Pediatrics", "medical office", 41.9201, -87.6440, 2400),
    _customer("Wicker Dental", "dental clinic", 41.9034, -87.6712, 2100),
    _customer("River North Law", "office", 41.8901, -87.6286, 3200),
    _customer("Edgewater Family Practice", "medical office", 41.9849, -87.6555, 2350),
]
# Food/fitness-heavy cleaner.
FOOD_BOOK = [
    _customer("Joe's Diner", "restaurant", 41.9201, -87.6440, 1500),
    _customer("Cafe Zed", "cafe", 41.9034, -87.6712, 950),
    _customer("Grand Grill", "restaurant", 41.8901, -87.6286, 1700),
    _customer("Loop Fitness", "fitness studio", 41.9849, -87.6555, 1600),
]


def _fresh_enriched_leads():
    """Load the fixture and geocode it offline into EnrichedLeads (fresh copies
    each call so drive-time annotation for one book doesn't leak into another)."""
    raw = load_fixture(FIXTURE)
    coords = batch_forward([f"{l.address}, {l.city}, {l.state}" for l in raw], offline=True)
    leads = []
    for lead, (lat, lng) in zip(raw, coords):
        e = EnrichedLead(**lead.model_dump(), lat=lat, lng=lng)
        if lat is not None and lng is not None:
            e.h3_cell = cell_for(lat, lng)
        leads.append(e)
    return leads


def _brief_for(book):
    leads = _fresh_enriched_leads()
    annotate_drive_times(leads, book)
    profile = seed_from_book("client", book)
    scored, cost = score_many(leads, book, profile=profile, as_of=AS_OF, top_n=0)
    assert cost == 0.0  # top_n=0 => no LLM spend
    return scored


def _rank_of(scored, needle):
    for i, s in enumerate(scored):
        if needle.lower() in s.name.lower():
            return i
    raise AssertionError(f"{needle!r} not in brief")


def test_same_fixture_two_books_rank_differently():
    med = _brief_for(MED_BOOK)
    food = _brief_for(FOOD_BOOK)
    med_order = [s.name for s in med]
    food_order = [s.name for s in food]
    assert med_order != food_order, "per-client tailoring produced identical ordering"


def test_food_leads_rank_higher_for_food_cleaner():
    med = _brief_for(MED_BOOK)
    food = _brief_for(FOOD_BOOK)
    # The coffee roaster (cafe) should sit higher in the food cleaner's brief.
    assert _rank_of(food, "Coffee Roasters") < _rank_of(med, "Coffee Roasters")


def test_medical_leads_rank_higher_for_medical_cleaner():
    med = _brief_for(MED_BOOK)
    food = _brief_for(FOOD_BOOK)
    # The dental studio should sit higher in the medical cleaner's brief.
    assert _rank_of(med, "Dental Studio") < _rank_of(food, "Dental Studio")


def test_scores_and_components_persisted_on_leads():
    med = _brief_for(MED_BOOK)
    top = med[0]
    assert top.tier in {"A", "B", "C", "drop"}
    assert top.signal_class in {"new_opening", "expansion", "churn_intent", "unknown"}
    # Component breakdown present for the brief/audit.
    assert top.margin_score is not None and 0 <= top.margin_score <= 10
    assert top.category_score is not None and top.route_score is not None
