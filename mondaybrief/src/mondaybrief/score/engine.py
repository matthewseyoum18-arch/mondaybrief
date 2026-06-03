"""Deterministic per-lead scoring engine.

This is the heart of the rebuild. It replaces the opaque single-LLM 0-100 score
with five transparent component sub-scores (each 0-10) combined under a
*per-client* weight vector, minus soft risk penalties, mapped to an A/B/C/drop
tier. Every number is auditable: given a lead, a profile, and this module you
can reconstruct the final score by hand.

No LLM, no network, no DB — pure functions of (lead, profile). That makes it
fast, free, reproducible, and unit-testable. The LLM (:mod:`score.narrative`)
runs afterward, only on the survivors, and writes the prose — it never touches
the number.

Component → weight key mapping (keys line up with ClientProfile.weights):
    margin        — estimated monthly margin $ if the account is won
    route         — drive-minutes off the cleaner's existing route
    category      — this client's desirability for the lead's category
    timing        — freshness of the trigger signal
    signal_class  — intent strength (new_opening > churn > expansion)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from ..models import EnrichedLead
from . import economics
from .profile import ClientProfile
from .signals import classify_signal
from .taxonomy import classify as classify_category


@dataclass
class ComponentScores:
    """The five 0-10 sub-scores behind a final score. Kept on the result so the
    brief (and any audit) can show *why* a lead scored what it did."""

    margin: float
    route: float
    category: float
    timing: float
    signal_class: float

    def as_dict(self) -> dict[str, float]:
        return {
            "margin": self.margin,
            "route": self.route,
            "category": self.category,
            "timing": self.timing,
            "signal_class": self.signal_class,
        }


@dataclass
class EngineResult:
    score: int
    tier: str
    components: ComponentScores
    margin_est_monthly: float
    category: str
    signal_class: str
    drive_minutes: Optional[float]
    risk: float
    # v2 signal-fusion outputs (None on the legacy/flag-off path).
    signal_confidence: Optional[float] = None
    corroboration_count: Optional[int] = None
    signal_evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Component scorers — each returns a float in [0, 10].
# ---------------------------------------------------------------------------
def margin_score(category: str | None, sqft: int | float | None) -> tuple[float, float]:
    """Return (score_0_10, estimated_monthly_margin_usd)."""
    est = economics.estimated_monthly_margin(category, sqft)
    for floor, score in economics.MARGIN_BANDS:  # descending floors
        if est >= floor:
            return score, est
    return 1.0, est


def route_score(drive_minutes: float | None) -> float:
    """Closer to an existing nightly stop = higher. Unknown drive time is
    scored neutrally (5.0) rather than punished, so a missing route estimate
    doesn't bury an otherwise strong lead."""
    if drive_minutes is None:
        return 5.0
    for ceiling, score in economics.ROUTE_BANDS:  # ascending ceilings
        if drive_minutes <= ceiling:
            return score
    return 0.5


def category_score(profile: ClientProfile, category: str | None) -> float:
    """This client's learned desirability for the category (0-10)."""
    return max(0.0, min(10.0, profile.preference_for(category)))


def timing_score(date_issued: date | None, as_of: date) -> float:
    """Exponential freshness decay. Today = 10; ~one half-life (21d) ago ≈ 5.
    Missing date is scored neutrally (5.0)."""
    if date_issued is None:
        return 5.0
    days_old = max(0, (as_of - date_issued).days)
    return round(10.0 * (0.5 ** (days_old / economics.TIMING_HALF_LIFE_DAYS)), 4)


def signal_class_score(signal_class: str, *, fused_p: float | None = None) -> float:
    """The 0-10 signal component. When a fused confidence is supplied (v2 layer),
    the component is ``10 × P_fused`` — a calibrated, corroborated, decay-aware
    value. When it is None (offline/unit tests or flag-off), fall back
    byte-identically to the v1 fixed per-class strength, preserving the legacy
    ordering (new_opening 10 > churn 9 > expansion 7 > unknown 5) and
    disqualified → 0.0."""
    if fused_p is not None:
        return round(10.0 * fused_p, 4)
    return economics.SIGNAL_CLASS_STRENGTH.get(signal_class, economics.SIGNAL_CLASS_STRENGTH["unknown"])


# ---------------------------------------------------------------------------
# Combine + tier
# ---------------------------------------------------------------------------
def combine(components: ComponentScores, weights: dict[str, float]) -> float:
    """Weighted average of the 0-10 components, normalized to [0, 1].

    Weights need not sum to 1 — we divide by their sum. A weight key missing
    from ``weights`` falls back to the global default so a partial profile can't
    silently zero a component.
    """
    comp = components.as_dict()
    total_w = 0.0
    acc = 0.0
    for key, value in comp.items():
        w = weights.get(key, economics.GLOBAL_DEFAULT_WEIGHTS.get(key, 0.0))
        acc += (value / 10.0) * w
        total_w += w
    if total_w <= 0:
        return 0.0
    return acc / total_w


def risk_penalty(
    profile: ClientProfile,
    category: str | None,
    contract_value: float,
    *,
    is_union: bool = False,
) -> float:
    """Soft penalties subtracted from the normalized [0,1] score. Soft, not a
    hard drop, so a strong-everything-else lead can still survive a single
    mismatch. ``contract_value`` is the GROSS estimated monthly contract value,
    compared against the (also gross) per-client floor."""
    penalty = 0.0
    if profile.excludes(category):
        penalty += economics.RISK_EXCLUDED_CATEGORY
    if profile.min_contract_monthly and contract_value < profile.min_contract_monthly:
        penalty += economics.RISK_BELOW_CONTRACT_FLOOR
    if is_union and "union" in {e.strip().lower() for e in profile.exclusions}:
        penalty += economics.RISK_UNION_EXCLUDED
    return penalty


def to_tier(score: int) -> str:
    if score >= economics.TIER_A_MIN:
        return "A"
    if score >= economics.TIER_B_MIN:
        return "B"
    if score >= economics.TIER_C_MIN:
        return "C"
    return "drop"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def _resolve_category(lead: EnrichedLead) -> str:
    raw = lead.raw_json or {}
    # NYC DCWP rows carry the vertical in `license_description`, not
    # `license_category`, so fall back to it for the structured NYC lookup.
    return classify_category(
        license_code=raw.get("license_code"),
        nyc_license_category=raw.get("license_category") or raw.get("license_description"),
        name=lead.name,
        description=raw.get("license_description") or raw.get("work_description"),
    )


_SQFT_RE = re.compile(r"([\d,]{2,})\s*(?:sq\.?\s*ft|square\s*feet|sf\b)", re.IGNORECASE)


def _resolve_sqft(lead: EnrichedLead) -> int | None:
    raw = lead.raw_json or {}
    for key in ("sqft", "square_feet", "squarefeet", "floor_area", "total_area"):
        val = raw.get(key)
        if val:
            try:
                return int(float(val))
            except (TypeError, ValueError):
                continue
    # NYC permit work_description often embeds literal sqft, e.g.
    # "interior fit-out, 12,000 sq ft office". Parse it before falling back to
    # the category median.
    for field in ("work_description", "license_description"):
        text = raw.get(field)
        if not text:
            continue
        m = _SQFT_RE.search(str(text))
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


def score_lead(
    lead: EnrichedLead,
    profile: ClientProfile,
    *,
    as_of: date | None = None,
    fusion: "object | None" = None,
) -> EngineResult:
    """Score one enriched lead against one client profile. Deterministic.

    ``lead.drive_minutes`` is expected to be populated upstream (enrich.drivetime).
    Category and signal class are resolved here from the raw row so callers don't
    have to pre-compute them.

    ``fusion`` is an optional :class:`score.signal_layer.FusionResult` for this
    lead's entity (built once per run by the pipeline). When supplied, the signal
    component is ``10 × P_fused`` and the coarse class + corroboration evidence
    come from the fusion; when None, the legacy per-class strength path runs
    unchanged. Typed loosely to avoid a hard import cycle.
    """
    as_of = as_of or date.today()
    category = _resolve_category(lead)
    sqft = _resolve_sqft(lead)

    if fusion is not None:
        signal_cls = fusion.signal_class
        sig_component = signal_class_score(signal_cls, fused_p=fusion.p_fused)
        signal_confidence = fusion.p_fused
        corroboration_count = fusion.corroboration_count
        signal_evidence = list(fusion.evidence)
    else:
        signal_cls = classify_signal(lead)
        sig_component = signal_class_score(signal_cls)
        signal_confidence = None
        corroboration_count = None
        signal_evidence = []

    m_score, est_monthly = margin_score(category, sqft)
    contract_value = economics.estimated_contract_value(category, sqft)
    components = ComponentScores(
        margin=m_score,
        route=route_score(lead.drive_minutes),
        category=category_score(profile, category),
        timing=timing_score(lead.date_issued, as_of),
        signal_class=sig_component,
    )

    raw = combine(components, profile.weights)
    is_union = bool((lead.raw_json or {}).get("union"))
    risk = risk_penalty(profile, category, contract_value, is_union=is_union)
    final01 = max(0.0, min(1.0, raw - risk))
    score = int(round(100 * final01))

    return EngineResult(
        score=score,
        tier=to_tier(score),
        components=components,
        margin_est_monthly=est_monthly,
        category=category,
        signal_class=signal_cls,
        drive_minutes=lead.drive_minutes,
        risk=round(risk, 4),
        signal_confidence=signal_confidence,
        corroboration_count=corroboration_count,
        signal_evidence=signal_evidence,
    )
