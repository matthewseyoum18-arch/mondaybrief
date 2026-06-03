"""Per-source signal detectors.

A detector is a PURE function ``detect(lead) -> list[Signal]`` that reads only
``lead.source`` + ``lead.raw_json`` + ``lead.date_issued`` (no DB, no network, no
LLM, no clock). It returns ``[]`` when the lead is not its source, may return
MORE THAN ONE Signal for one row, and NEVER decides the final score — it only
describes evidence. :func:`run_detectors` fans a lead across every detector,
unions the Signals, always runs the negative-flag detector, and emits a single
``unknown`` Signal when nothing positive matched (so the lead still flows but
stays below the confidence floor unless corroborated).

Detectors are table-driven and reuse the seeded specs in
:mod:`score.economics` via :func:`score.signal_layer.make_signal`, so adding a
source is a few lines here plus a spec row there.

Currently-wired sources emit real signals today; sources not yet ingested
(liquor ``nrmj-3kcf``, NYC CO ``pkdm-hqz6``, food ``43nn-pn8j``, parcel
``wvhk-k5uv``, eviction ``6z8x-wfk4``) have detectors here that stay inert until
their ingest module lands — the layer degrades gracefully to wired sources.
"""
from __future__ import annotations

from typing import Callable, Optional

from ..models import RawLead
from .signal_layer import Signal, make_signal, make_negative_signal
from .taxonomy import GENERIC_LICENSE_CODES, DEFAULT_CATEGORY, classify as classify_category


def _raw(lead: RawLead, key: str, default: str = "") -> str:
    val = (lead.raw_json or {}).get(key)
    return str(val).strip() if val is not None else default


# Status tokens that mean "this business is dead" — hard veto on any source.
_DEAD_STATUS = {"REV", "AAC", "REVOKED", "CANCELLED", "CLOSED", "Q", "W", "X"}

# NYC DCWP license_category values that imply a real cleanable physical premise.
_NYC_PREMISE_ALLOW = {
    "Sidewalk Cafe", "Restaurant", "Catering Establishment", "Garage and Parking Lot",
    "Newsstand", "Laundries", "Pharmacy", "Grocery-Retail", "Gas Station",
}


# ---------------------------------------------------------------------------
# Chicago
# ---------------------------------------------------------------------------
def detect_chicago_license(lead: RawLead) -> list[Signal]:
    """r5kz-chrr Business Licenses. A specific cleanable license is a strong
    coincident new-opening; a generic 'Limited Business License' is weak and
    needs corroboration."""
    if lead.source != "r5kz-chrr":
        return []
    if _raw(lead, "application_type").upper() not in ("", "ISSUE"):
        # change-of-location / renewal handled elsewhere; only ISSUE is a new open
        if _raw(lead, "application_type").upper() in ("C_LOC", "C_EXPA", "C_CAPA"):
            return [make_signal("chi_permit_alteration", lead, label="business expansion filing")]
        return []
    code = _raw(lead, "license_code")
    if code and code in GENERIC_LICENSE_CODES:
        # A generic "Limited Business License" describes the license FORM, not the
        # business vertical — weak on its own. BUT when the business NAME resolves
        # to a specific cleanable vertical (Dental Studio, Vet Clinic, Pilates),
        # the name IS the evidence: treat it as a real new-business signal. Only a
        # license whose name tells us nothing (resolves to the generic default)
        # stays weak and needs corroboration to clear the floor.
        cat = classify_category(
            license_code=code,
            name=lead.dba or lead.name,
            description=_raw(lead, "license_description") or None,
        )
        if cat == DEFAULT_CATEGORY:
            return [make_signal("chi_license_limited", lead, label="new business license (limited)")]
        return [make_signal("chi_license_issue", lead, label="new business license issued")]
    return [make_signal("chi_license_issue", lead, label="new business license issued")]


def detect_chicago_permit(lead: RawLead) -> list[Signal]:
    """ydr8-5enu Building Permits."""
    if lead.source != "ydr8-5enu":
        return []
    ptype = _raw(lead, "permit_type")
    if ptype == "PERMIT - NEW CONSTRUCTION":
        return [make_signal("chi_permit_newcon", lead, label="new-construction permit")]
    if ptype == "PERMIT - RENOVATION/ALTERATION":
        return [make_signal("chi_permit_alteration", lead, label="renovation/alteration permit")]
    if ptype == "PERMIT - SIGNS":
        return [make_signal("chi_permit_sign", lead, label="new sign permit")]
    if ptype == "PERMIT - WRECKING/DEMOLITION":
        return [make_negative_signal("demolition", lead, label="demolition permit")]
    return []


def detect_chicago_food_inspection(lead: RawLead) -> list[Signal]:
    """4ijn-s7e5. NOTE (dataset-identity fix): on the Chicago portal this ID is
    the FOOD INSPECTIONS dataset, not liquor. A row with
    ``inspection_type='License'`` is a brand-new food establishment opening — a
    very strong coincident signal (0.88). Routine inspection types
    (Canvass/Complaint) are NOT opening signals and emit nothing. Legacy
    liquor-shaped fixture rows (no ``inspection_type`` field) are treated as a
    liquor new-issuance for back-compat. Chicago liquor/PPA proper lives at
    ``nrmj-3kcf`` (see :func:`detect_chicago_liquor`)."""
    if lead.source != "4ijn-s7e5":
        return []
    # Discriminate on field PRESENCE, not truthiness: a real food-inspection row
    # carries an `inspection_type` key (possibly blank on a routine row), whereas
    # a legacy liquor-shaped fixture row has no such key at all. A blank/None
    # inspection_type on a food row is a routine inspection → emit nothing (NOT a
    # 0.80 liquor opening).
    rj = lead.raw_json or {}
    if "inspection_type" in rj:
        insp = _raw(lead, "inspection_type").lower()
        if "license" in insp:
            return [make_signal("chi_food_license", lead, label="new food establishment (license inspection)")]
        return []
    # Legacy liquor-shaped row (license_start_date / doing_business_as).
    return [make_signal("chi_liquor_new", lead, label="new liquor/amusement license")]


def detect_chicago_liquor(lead: RawLead) -> list[Signal]:
    """nrmj-3kcf Chicago Liquor / Public Place of Amusement (correct dataset).
    Inert until ingest lands."""
    if lead.source != "nrmj-3kcf":
        return []
    return [make_signal("chi_liquor_new", lead, label="new liquor/amusement license")]


def detect_chicago_parcel_sale(lead: RawLead) -> list[Signal]:
    """wvhk-k5uv Cook County parcel sales (commercial). Inert until ingest."""
    if lead.source != "wvhk-k5uv":
        return []
    return [make_signal("parcel_sale", lead, label="commercial property sale")]


# ---------------------------------------------------------------------------
# NYC
# ---------------------------------------------------------------------------
def detect_nyc_permit(lead: RawLead) -> list[Signal]:
    """nyc:ipu4-2q9a DOB Now permits."""
    if lead.source != "nyc:ipu4-2q9a":
        return []
    jt = _raw(lead, "job_type").upper()
    if jt == "NB":
        return [make_signal("nyc_dob_nb", lead, label="new-building permit")]
    if jt in ("A1", "A2", "A3"):
        return [make_signal("nyc_dob_alt", lead, label="alteration permit")]
    if jt == "DM":
        return [make_negative_signal("demolition", lead, label="demolition job")]
    return []


def detect_nyc_license(lead: RawLead) -> list[Signal]:
    """nyc:ic3t-wcy2 DCWP Legally Operating Businesses."""
    if lead.source != "nyc:ic3t-wcy2":
        return []
    cat = _raw(lead, "license_category") or _raw(lead, "license_description")
    if cat in _NYC_PREMISE_ALLOW:
        return [make_signal("nyc_dcwp_premise", lead, label="new NYC business license")]
    return [make_signal("nyc_dcwp_unfiltered", lead, label="new NYC business license")]


def detect_nyc_cert_occupancy(lead: RawLead) -> list[Signal]:
    """pkdm-hqz6 NYC DOB NOW Certificate of Occupancy. Inert until ingest."""
    if lead.source != "pkdm-hqz6":
        return []
    return [make_signal("nyc_co", lead, label="certificate of occupancy")]


def detect_nyc_food(lead: RawLead) -> list[Signal]:
    """43nn-pn8j NYC restaurant inspections. The sentinel inspection_date
    1900-01-01 marks a brand-new, not-yet-inspected establishment. Inert until
    ingest."""
    if lead.source != "43nn-pn8j":
        return []
    if _raw(lead, "inspection_date").startswith("1900-01-01"):
        return [make_signal("nyc_food_new", lead, label="new NYC food establishment")]
    return []


def detect_nyc_eviction(lead: RawLead) -> list[Signal]:
    """6z8x-wfk4 NYC evictions — a commercial eviction frees a space for a new
    occupant (weak expansion lead), NOT churn of the evicted tenant. Inert until
    ingest."""
    if lead.source != "6z8x-wfk4":
        return []
    return [make_signal("eviction_commercial", lead, label="commercial space turning over")]


# ---------------------------------------------------------------------------
# Market / churn (tag-prefixed synthetic sources) + reputation (inert)
# ---------------------------------------------------------------------------
def detect_market_rfp(lead: RawLead) -> list[Signal]:
    """Janitorial RFP / bid-board signals, ingested under a ``rfp_board:`` tag.
    Decision #2: low-ToS public boards only. The date_event for an RFP should be
    its due_date (set upstream as ``date_issued``) so decay runs against the
    deadline."""
    src = lead.source.lower()
    if not (src.startswith("rfp_board:") or src.startswith("passport") or src.startswith("crol")):
        return []
    if "passport" in src or "crol" in src:
        return [make_signal("rfp_passport", lead, label="NYC janitorial re-bid (PASSPort/CROL)")]
    if "sam" in src or "govt" in src or "gov" in src:
        return [make_signal("rfp_govt", lead, label="government janitorial RFP")]
    return [make_signal("rfp_demandstar", lead, label="local janitorial RFP")]


def detect_reputation(lead: RawLead) -> list[Signal]:
    """review_drop:/bbb: tags. INERT BY DESIGN — prior 0.0, ToS-blocked. The hook
    exists so the class is representable, never to be fed (do not scrape Yelp/
    Google/BBB/Glassdoor — see project_oss_no_go_list)."""
    src = lead.source.lower()
    if not (src.startswith("review_drop:") or src.startswith("bbb:")):
        return []
    return [make_signal("review_drop", lead, label="review/complaint signal (inert)")]


# ---------------------------------------------------------------------------
# Negative flags — run on EVERY lead regardless of source
# ---------------------------------------------------------------------------
def detect_negative_flags(lead: RawLead) -> list[Signal]:
    """Cross-source negatives. Unambiguous death = hard veto; soft cooling
    signals = negative woe (decision #3). Most leads emit nothing here."""
    out: list[Signal] = []
    rj = lead.raw_json or {}

    for key in ("status", "license_status", "filing_status"):
        if _raw(lead, key).upper() in _DEAD_STATUS:
            out.append(make_negative_signal("license_revoked", lead, label="license revoked/closed"))
            break

    if rj.get("bankruptcy") is True:
        out.append(make_negative_signal("bankruptcy", lead, label="bankruptcy filing"))
    if rj.get("inhouse_janitor") is True:
        out.append(make_negative_signal("inhouse_hire", lead, label="hiring in-house custodian"))

    # Soft cooling signals (mostly future ingest; inert today unless flagged).
    if rj.get("ownership_change") is True:
        out.append(make_negative_signal("ownership_change", lead, label="recent ownership change"))
    if rj.get("expired_license") is True:
        out.append(make_negative_signal("expired_license", lead, label="license expired (not renewed)"))
    if rj.get("lawsuit") is True:
        out.append(make_negative_signal("lawsuit", lead, label="lawsuit on file"))
    if rj.get("lien") is True:
        out.append(make_negative_signal("lien", lead, label="lien on file"))
    return out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
POSITIVE_DETECTORS: tuple[Callable[[RawLead], list[Signal]], ...] = (
    detect_chicago_license,
    detect_chicago_permit,
    detect_chicago_food_inspection,
    detect_chicago_liquor,
    detect_chicago_parcel_sale,
    detect_nyc_permit,
    detect_nyc_license,
    detect_nyc_cert_occupancy,
    detect_nyc_food,
    detect_nyc_eviction,
    detect_market_rfp,
    detect_reputation,
)


def run_detectors(lead: RawLead, ctx: Optional[dict] = None) -> list[Signal]:
    """All Signals for one lead. Always includes negative flags; falls back to a
    single ``unknown`` Signal when nothing positive matched, so the lead still
    flows through fusion but cannot clear the floor without corroboration."""
    signals: list[Signal] = []
    for det in POSITIVE_DETECTORS:
        signals.extend(det(lead))
    negatives = detect_negative_flags(lead)
    signals.extend(negatives)

    has_positive = any(not s.is_negative for s in signals)
    if not has_positive and not any(s.is_hard_disqualify for s in signals):
        signals.append(make_signal("unknown", lead, label="uncorroborated record"))
    return signals
