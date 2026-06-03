"""The per-client proof.

The whole point of the rebuild: the SAME lead must score differently for
different cleaners, in the right direction, and a whole week's lead-set must
rank differently per client. If these pass, MondayBrief's leads are genuinely
tailored to the business they're sent to — not one global rubric.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mondaybrief.models import Customer, EnrichedLead  # noqa: E402
from mondaybrief.score.engine import score_lead  # noqa: E402
from mondaybrief.score.profile import seed_from_book  # noqa: E402

AS_OF = date(2026, 6, 1)

OFFICE_BOOK = [
    Customer(client_id="office_co", name="Acme Suites", address="1 W Wacker", city="Chicago", state="IL", category="office", monthly_rev=1800),
    Customer(client_id="office_co", name="Beta Partners", address="2 N LaSalle", city="Chicago", state="IL", category="professional", monthly_rev=2200),
    Customer(client_id="office_co", name="Gamma Group", address="3 S Dearborn", city="Chicago", state="IL", category="office", monthly_rev=1600),
]
RESTO_BOOK = [
    Customer(client_id="resto_co", name="Joe's Diner", address="9 W Randolph", city="Chicago", state="IL", category="restaurant", monthly_rev=1300),
    Customer(client_id="resto_co", name="Cafe Zed", address="8 W Lake", city="Chicago", state="IL", category="cafe", monthly_rev=900),
    Customer(client_id="resto_co", name="Pier Grill", address="7 E Grand", city="Chicago", state="IL", category="restaurant", monthly_rev=1500),
]


def _lead(category, *, drive=6.0, sqft=None):
    rj = {"license_description": category}
    if sqft:
        rj["sqft"] = sqft
    return EnrichedLead(
        source="r5kz-chrr", source_id=category, name=f"New {category}",
        address="100 W Madison St", city="Chicago", state="IL",
        date_issued=date(2026, 5, 28), raw_json=rj, drive_minutes=drive,
    )


def test_same_office_lead_favours_office_cleaner():
    office_p = seed_from_book("office_co", OFFICE_BOOK)
    resto_p = seed_from_book("resto_co", RESTO_BOOK)
    lead = _lead("office", drive=6)
    assert score_lead(lead, office_p, as_of=AS_OF).score > score_lead(lead, resto_p, as_of=AS_OF).score


def test_same_restaurant_lead_favours_restaurant_cleaner():
    office_p = seed_from_book("office_co", OFFICE_BOOK)
    resto_p = seed_from_book("resto_co", RESTO_BOOK)
    lead = _lead("restaurant", drive=6)
    assert score_lead(lead, resto_p, as_of=AS_OF).score > score_lead(lead, office_p, as_of=AS_OF).score


def test_weekly_leadset_ranks_differently_per_client():
    """A mixed week's leads rank in a materially different order per client."""
    office_p = seed_from_book("office_co", OFFICE_BOOK)
    resto_p = seed_from_book("resto_co", RESTO_BOOK)
    leads = [
        _lead("office", drive=6),
        _lead("restaurant", drive=6),
        _lead("cafe", drive=8),
        _lead("dental clinic", drive=10, sqft=3000),
        _lead("retail", drive=12),
    ]

    def ranked(profile):
        scored = [(lead.raw_json["license_description"], score_lead(lead, profile, as_of=AS_OF).score) for lead in leads]
        return [name for name, _ in sorted(scored, key=lambda t: -t[1])]

    office_order = ranked(office_p)
    resto_order = ranked(resto_p)

    # The orders must differ — same leads, different priorities.
    assert office_order != resto_order
    # And specifically: office ranks 'office' above 'restaurant'; resto the reverse.
    assert office_order.index("office") < office_order.index("restaurant")
    assert resto_order.index("restaurant") < resto_order.index("office")


def test_component_breakdown_is_exposed_for_audit():
    office_p = seed_from_book("office_co", OFFICE_BOOK)
    res = score_lead(_lead("office", drive=6), office_p, as_of=AS_OF)
    comp = res.components.as_dict()
    # Every component present and in range — the brief can show the math.
    assert set(comp) == {"margin", "route", "category", "timing", "signal_class"}
    assert all(0.0 <= v <= 10.0 for v in comp.values())
