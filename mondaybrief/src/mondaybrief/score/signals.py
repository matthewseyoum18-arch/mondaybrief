"""Signal class detection.

Three signal classes equally weighted in v1 per
``project_mondaybrief_icp_signals`` memory:

1. ``new_opening``     — net-new business is opening doors in 30-90 days.
                         Vendor selection window is RIGHT NOW. Highest
                         meeting-book conversion intent.
2. ``expansion``       — existing business is adding sqft / locations /
                         headcount. Already has a cleaner; may need to
                         absorb new square footage or replace if scale
                         outgrows current vendor.
3. ``churn_intent``    — existing business is unhappy with current
                         cleaner. Hardest to detect (requires external
                         signals: reviews, BBB, RFP boards). v1 wires
                         the slot; full ingest comes in v1.1.

Plus:

4. ``unknown``         — couldn't classify. Default category.
5. ``disqualified``    — bankruptcy / foreclosure / closed / revoked.
                         Pipeline drops these BEFORE scoring.

Classification rules (deterministic, no LLM):

* Chicago ``r5kz-chrr`` (Business Licenses) with ``application_type='ISSUE'``
  → ``new_opening``. Renewals (``RENEW``) already filtered at ingest.
* NYC ``ic3t-wcy2`` (DCWP Legally Operating Businesses) with first issue
  date inside the lookback window → ``new_opening``. Amendments adding a
  new location → ``expansion`` (DCWP supports per-location records).
* Chicago ``ydr8-5enu`` (Building Permits) with
  ``permit_type='PERMIT - NEW CONSTRUCTION'`` → ``new_opening``.
* Chicago ``ydr8-5enu`` with
  ``permit_type='PERMIT - RENOVATION/ALTERATION'`` → ``expansion``.
* NYC ``ipu4-2q9a`` (DOB Now Permits) with ``job_type='NB'`` → ``new_opening``.
* NYC ``ipu4-2q9a`` with ``job_type in ('A1','A2','A3')`` → ``expansion``.
* Chicago ``4ijn-s7e5`` (Liquor / Public Amusement applications) → ``new_opening``.
* Anything tagged ``rfp_board:`` or ``review_drop:`` (future ingest) → ``churn_intent``.

Negative-signal flags that disqualify a lead:

* ``raw_json.status == 'REV'`` (revoked) → ``disqualified``
* ``raw_json.license_status == 'AAC'`` (active cancelled) → ``disqualified``
* ``raw_json.filing_status in ('Q','W','X')`` (NYC withdrawn/closed) → ``disqualified``
* ``raw_json.bankruptcy == True`` (explicit flag from future court-record ingest) → ``disqualified``

The classifier reads only ``RawLead.source`` and ``RawLead.raw_json`` —
no network calls, no DB. Use ``classify_signal`` at the top of the
scoring step; ``disqualified`` returns short-circuit the rest of the
pipeline for that lead.
"""
from __future__ import annotations

from typing import Literal

from ..models import RawLead


SignalClass = Literal[
    "new_opening",
    "expansion",
    "churn_intent",
    "unknown",
    "disqualified",
]


# Sources that map straight to ``new_opening`` regardless of metadata
# (already filtered to fresh issues at the Socrata WHERE clause).
_NEW_OPENING_SOURCES = {
    "r5kz-chrr",     # Chicago business licenses (filtered AAI + ISSUE)
    "4ijn-s7e5",     # Chicago liquor / public amusement
    "nyc:ic3t-wcy2", # NYC DCWP licenses (filtered Active)
}

# Chicago building permit type → signal class
_CHI_PERMIT_TYPE_MAP = {
    "PERMIT - NEW CONSTRUCTION":      "new_opening",
    "PERMIT - WRECKING/DEMOLITION":   "disqualified",  # demolition = no future tenant
    "PERMIT - RENOVATION/ALTERATION": "expansion",
    "PERMIT - SCAFFOLDING":           "unknown",
    "PERMIT - SIGNS":                 "new_opening",   # new sign = new business
}

# NYC DOB Now job_type → signal class
_NYC_JOB_TYPE_MAP = {
    "NB": "new_opening",   # New Building
    "A1": "expansion",     # Alteration Type 1 (major)
    "A2": "expansion",     # Alteration Type 2 (medium)
    "A3": "expansion",     # Alteration Type 3 (minor)
    "DM": "disqualified",  # Demolition
}

# Status flags that mean "drop this lead"
_DISQUALIFIED_STATUS = {
    "REV",                  # Chicago revoked
    "AAC",                  # Chicago cancelled
    "Q", "W", "X",          # NYC withdrawn / closed / void
    "REVOKED", "CANCELLED", "CLOSED",
}


def _raw(lead: RawLead, key: str, default: str = "") -> str:
    """Helper: pull a string field from raw_json with empty-string default."""
    val = (lead.raw_json or {}).get(key)
    return str(val).strip() if val is not None else default


def _is_disqualified(lead: RawLead) -> bool:
    """Negative-signal check. Returns True if the lead should drop."""
    # Explicit status flags
    for key in ("status", "license_status", "filing_status"):
        if _raw(lead, key).upper() in _DISQUALIFIED_STATUS:
            return True

    # Bankruptcy flag from future court-record ingest
    if (lead.raw_json or {}).get("bankruptcy") is True:
        return True

    # Chicago demolition permit
    if _raw(lead, "permit_type") == "PERMIT - WRECKING/DEMOLITION":
        return True

    # NYC demolition job
    if _raw(lead, "job_type") == "DM":
        return True

    return False


def classify_signal(lead: RawLead) -> SignalClass:
    """Map a RawLead to one of the five signal classes.

    Reads source + raw_json only. Deterministic — same input always
    yields same output. Safe to call on every lead before scoring.
    """
    if _is_disqualified(lead):
        return "disqualified"

    source = lead.source.lower()

    # Future ingest tags (RFP boards, review-drop monitors)
    if source.startswith("rfp_board:") or source.startswith("review_drop:") or source.startswith("bbb:"):
        return "churn_intent"

    # Source-only mappings
    if source in _NEW_OPENING_SOURCES:
        return "new_opening"

    # Chicago building permits
    if source == "ydr8-5enu":
        ptype = _raw(lead, "permit_type")
        return _CHI_PERMIT_TYPE_MAP.get(ptype, "unknown")  # type: ignore[return-value]

    # NYC DOB Now permits
    if source == "nyc:ipu4-2q9a":
        jtype = _raw(lead, "job_type")
        return _NYC_JOB_TYPE_MAP.get(jtype, "unknown")  # type: ignore[return-value]

    return "unknown"


# Per-class scoring multiplier used by claude_score to bias the 0-100
# score band. Values calibrated so a great-fit new_opening can score
# 95, a great-fit expansion 88, and a great-fit churn_intent 92 (warmer
# than expansion because the buyer is already actively shopping).
SIGNAL_CLASS_MULTIPLIER: dict[str, float] = {
    "new_opening":  1.00,
    "expansion":    0.92,
    "churn_intent": 0.97,
    "unknown":      0.85,
    "disqualified": 0.0,   # short-circuit
}


def signal_class_weight(cls: SignalClass) -> float:
    """Scoring multiplier for a signal class. Used by claude_score to
    bias the final 0-100 score; never used to mutate raw Claude output."""
    return SIGNAL_CLASS_MULTIPLIER.get(cls, 0.85)
