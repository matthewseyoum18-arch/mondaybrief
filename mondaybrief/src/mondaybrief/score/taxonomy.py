"""Deterministic license-code -> canonical category taxonomy.

Replaces the regex-soup in ``pipeline._guess_category`` with an explicit
mapping table keyed on the real license codes published by the Chicago
Business Licenses dataset (``r5kz-chrr``) and the NYC DCWP Legally
Operating Businesses dataset (``ic3t-wcy2``).

Resolution priority inside :func:`classify`:

  1. A *specific* Chicago ``license_code`` (e.g. ``"1475"`` Retail Food).
     Generic "Limited Business License" codes (``1006``/``1010``) are
     skipped here — they describe the license form, not the vertical.
  2. NYC ``license_category`` (a human-readable string like
     ``"Sidewalk Cafe"``).
  3. Free-text keyword match on the business name + license/work
     description — this is what classifies a "Dental Studio" or "Vet
     Clinic" filed under a generic code.
  4. A *generic* code's soft mapping (``1006`` -> ``"office"``) when no
     keyword matched — better than the bare default for an unnamed shell.
  5. ``"office"`` — the safe default. Cleaning a generic office is the
     most common shape of a v1 MondayBrief lead, so when the taxonomy
     can't say anything sharper we still emit something the scorer and
     opener templates can render against.

The keyword list intentionally favors precision over recall — it only
fires on tokens that almost never appear in unrelated names (``dental``,
``pilates``, ``crossfit``). Anything ambiguous (``health``, ``services``)
is left out so we don't mis-tag.

Sources:
  - Chicago Business Licenses dataset: data.cityofchicago.org/dataset/Business-Licenses/r5kz-chrr
  - NYC Legally Operating Businesses: data.cityofnewyork.us/Business/Legally-Operating-Businesses/ic3t-wcy2
"""
from __future__ import annotations
from typing import Optional


# ---------------------------------------------------------------------------
# Chicago r5kz-chrr ``license_code`` -> canonical category.
#
# Codes are short numeric strings as returned by the Socrata API. The set
# below covers the long tail of cleaner-relevant license shapes; anything
# not listed falls through to ``"office"`` via :func:`classify`.
# ---------------------------------------------------------------------------
LICENSE_CODE_TO_CATEGORY: dict[str, str] = {
    "1006": "office",             # Limited Business License
    "1010": "professional",       # Limited Business / regulated overlap
    "1475": "cafe",               # Retail Food Establishment
    "1471": "restaurant",         # Retail Food Establishment - Tobacco
    "1474": "cafe",               # Mobile Food License
    "1050": "office",             # Public Place of Amusement
    "1623": "restaurant",         # Tavern
    "1624": "restaurant",         # Consumption on Premises - Incidental Activity
    "1009": "retail",             # Retail Food Establishment
    "1011": "retail",             # Wholesale Food Establishment
    "1007": "office",             # Day Care
    "1008": "fitness studio",     # Health & Fitness
    "1090": "medical office",     # Medical Office Building (custom mapping)
    "1018": "professional",       # Professional Service
}

# Generic "Limited Business License"-family codes that describe the license
# *form*, not the business *vertical*. A dental clinic, a law office, and a yoga
# studio can all hold a 1006. For these we must NOT let the code override the
# name/description (otherwise "Lincoln Park Dental Studio" scores as a generic
# office). They are used only as a soft fallback when no keyword matches.
GENERIC_LICENSE_CODES: set[str] = {"1006", "1010"}


# ---------------------------------------------------------------------------
# NYC ic3t-wcy2 ``license_category`` -> canonical category.
#
# This dataset uses descriptive strings rather than numeric codes, so the
# keys are the literal values you'll see on a row.
# ---------------------------------------------------------------------------
NYC_LICENSE_CATEGORY_TO_CATEGORY: dict[str, str] = {
    "Tobacco Retail Dealer": "retail",
    "Sidewalk Cafe": "cafe",
    "Restaurant": "restaurant",
    "Catering Establishment": "restaurant",
    "Tow Truck Driver": "other",
    "Newsstand": "retail",
    "Garage and Parking Lot": "office",
    "Laundries": "other",
    "Pedicab Business": "other",
}


# ---------------------------------------------------------------------------
# Keyword fallback. Ordered: earlier entries win on first match. Tokens
# are matched as case-insensitive substrings against ``name`` first, then
# ``description``.
# ---------------------------------------------------------------------------
KEYWORD_FALLBACK: list[tuple[str, str]] = [
    ("dental", "dental clinic"),
    ("dentist", "dental clinic"),
    ("orthodont", "dental clinic"),
    ("vet", "vet clinic"),
    ("animal hospital", "vet clinic"),
    ("medical", "medical office"),
    ("clinic", "medical office"),
    ("pediatric", "medical office"),
    ("imaging", "medical office"),
    ("optomet", "medical office"),
    ("chiroprac", "medical office"),
    ("pilates", "fitness studio"),
    ("yoga", "fitness studio"),
    ("gym", "fitness studio"),
    ("crossfit", "fitness studio"),
    ("fitness", "fitness studio"),
    ("cafe", "cafe"),
    ("coffee", "cafe"),
    ("roaster", "cafe"),
    ("bakery", "cafe"),
    ("restaurant", "restaurant"),
    ("tavern", "restaurant"),
    ("bar &", "restaurant"),
    ("retail", "retail"),
    ("salon", "retail"),
    ("barber", "retail"),
]


DEFAULT_CATEGORY = "office"


def classify(
    *,
    license_code: Optional[str] = None,
    nyc_license_category: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """Resolve a canonical MondayBrief category for one lead.

    Priority: ``license_code`` -> ``nyc_license_category`` ->
    keyword match on ``name`` / ``description`` -> ``"office"``.

    All inputs are optional so callers can pass through whatever the
    upstream raw row happens to carry without pre-checking for nulls.
    """
    code = str(license_code).strip() if license_code else None

    # 1. Chicago structured license_code — but ONLY when it is specific. A
    #    generic "Limited Business License" tells us nothing about the vertical,
    #    so we let the name/description decide first and use the code as a soft
    #    fallback below.
    if code and code in LICENSE_CODE_TO_CATEGORY and code not in GENERIC_LICENSE_CODES:
        return LICENSE_CODE_TO_CATEGORY[code]

    # 2. NYC structured license_category.
    if nyc_license_category:
        key = str(nyc_license_category).strip()
        if key in NYC_LICENSE_CATEGORY_TO_CATEGORY:
            return NYC_LICENSE_CATEGORY_TO_CATEGORY[key]

    # 3. Keyword fallback on name + description. This is what catches a
    #    "Dental Studio" / "Vet Clinic" / "Pilates Loft" filed under a generic code.
    haystacks: list[str] = []
    if name:
        haystacks.append(name.lower())
    if description:
        haystacks.append(description.lower())
    for keyword, category in KEYWORD_FALLBACK:
        for hay in haystacks:
            if keyword in hay:
                return category

    # 4. Soft fallback: a generic code's mapping (e.g. 1006 -> office) when no
    #    keyword matched. Better than the bare default for an unnamed shell LLC.
    if code and code in LICENSE_CODE_TO_CATEGORY:
        return LICENSE_CODE_TO_CATEGORY[code]

    # 5. Default.
    return DEFAULT_CATEGORY
