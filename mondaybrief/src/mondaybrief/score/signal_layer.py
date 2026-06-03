"""Deterministic signal-fusion layer — the heart of the v2 signaling system.

The naive v1 layer mapped one public record to one coarse class and one fixed
strength. This module turns every record into one or more calibrated
:class:`Signal` objects and FUSES the signals that land on the same business
(name + address) into a single probability ``P_fused`` via Naive-Bayes /
weight-of-evidence log-odds summation:

    logit(P) = logit(base_rate) + Σ_families clamp(family_woe, ±WOE_CLAMP)

* CORROBORATION falls out for free: independent families (a license + a permit +
  an RFP on the same entity) add positive evidence and compound toward A-tier.
* CONTRADICTION falls out for free: a demolition / revoked / closed signal is a
  HARD veto (P_fused → 0); softer negatives (eviction, lawsuit, lien) add
  negative weight-of-evidence that down-weights without nuking a strong lead.
* DOUBLE-COUNTING is guarded: positives take the MAX within a source-family and
  SUM across families, so correlated rows from one construction project don't
  manufacture confidence.

Everything here is a PURE function of its inputs — no DB, no network, no LLM, no
clock except the ``as_of`` passed in — so it is fast, free, reproducible, and
unit-testable, matching :mod:`score.engine`'s design ethos. Constants live in
:mod:`score.economics`; the per-source detectors that emit Signals live in
:mod:`score.detectors`. Full design: vault note "MondayBrief Signal Fusion
System v2".
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from ..models import RawLead
from . import economics


# ---------------------------------------------------------------------------
# Math primitives
# ---------------------------------------------------------------------------
def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def logit(p: float) -> float:
    """log-odds of a probability, guarded away from 0/1 so it stays finite."""
    p = _clamp(p, 1e-6, 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


def sigmoid(x: float) -> float:
    """Inverse of :func:`logit`. Numerically stable for large |x|."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def woe_for_precision(precision: float) -> float:
    """Weight of evidence a signal of this precision carries vs the base rate:
    ``clamp(logit(precision) − logit(base_rate), ±WOE_CLAMP)``. Positive when the
    signal is more predictive than a random row; ~0 when it equals the base
    rate; never beyond ±WOE_CLAMP so one source can't swamp the fusion."""
    raw = logit(precision) - economics.BASE_LOGIT
    return _clamp(raw, -economics.WOE_CLAMP, economics.WOE_CLAMP)


# ---------------------------------------------------------------------------
# Entity resolution — decision #5: corroborate only when name AND address match.
# Strict-ish normalized equality (deterministic, no fuzzy linkage). Costs some
# real merges where a legal name and a DBA differ; in exchange a multi-tenant
# tower can never fuse two different businesses into one inflated lead.
# ---------------------------------------------------------------------------
_BIZ_SUFFIXES = {
    "llc", "inc", "incorporated", "corp", "corporation", "co", "company",
    "ltd", "limited", "lp", "llp", "pllc", "pc", "pa", "group",
}
_ADDR_ABBREV = {
    "street": "st", "avenue": "ave", "av": "ave", "boulevard": "blvd",
    "drive": "dr", "road": "rd", "lane": "ln", "court": "ct", "place": "pl",
    "square": "sq", "terrace": "ter", "parkway": "pkwy", "highway": "hwy",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "suite": "ste", "floor": "fl", "apartment": "apt", "unit": "unit",
}
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def _normalize_name(name: Optional[str]) -> str:
    if not name:
        return ""
    s = _PUNCT_RE.sub(" ", name.lower())
    toks = [t for t in _WS_RE.sub(" ", s).strip().split(" ") if t]
    if toks and toks[0] == "the":
        toks = toks[1:]
    while toks and toks[-1] in _BIZ_SUFFIXES:
        toks = toks[:-1]
    return " ".join(toks)


def _normalize_address(address: Optional[str]) -> str:
    if not address:
        return ""
    s = _PUNCT_RE.sub(" ", address.lower())
    toks = [_ADDR_ABBREV.get(t, t) for t in _WS_RE.sub(" ", s).strip().split(" ") if t]
    return " ".join(toks)


def resolve_entity_key(name: Optional[str], address: Optional[str], city: Optional[str] = None) -> str:
    """Stable key for "the same business at the same place". Both the normalized
    name AND the normalized address must match for two signals to fuse."""
    return f"{_normalize_name(name)}|{_normalize_address(address)}|{(city or '').strip().lower()}"


def entity_key_for(lead: RawLead) -> str:
    """Entity key for a lead, with a degeneracy guard. If BOTH the normalized
    name and address are empty (a suffix-only name like "LLC" plus a blank
    address — common before geocoding or in messy feeds), there is nothing to
    match on, so fall back to a per-row unique key. Otherwise two unrelated
    businesses would collapse to ``"||city"`` and fusion would silently drop one
    of them."""
    key = resolve_entity_key(lead.dba or lead.name, lead.address, lead.city)
    name_part, addr_part, _ = key.split("|", 2)
    if not name_part and not addr_part:
        return f"__norow__:{lead.source}:{lead.source_id}"
    return key


# ---------------------------------------------------------------------------
# The Signal — the atomic unit fusion operates on
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Signal:
    """One calibrated piece of evidence about one business. Detectors emit these;
    :func:`fuse` combines them. Coarse CLASS (for narrative/back-compat),
    calibrated CONFIDENCE (precision_prior/woe, for ranking + the floor), and
    TIMING (date_event/lead_time/half_life, for the decay curve) are deliberately
    separate so each can be tuned independently."""

    entity_key: str
    signal_type: str
    source: str
    source_family: str
    signal_class: str
    precision_prior: float
    woe: float
    date_event: Optional[date]
    lead_time_days: int
    half_life_days: float
    leading: bool
    is_negative: bool = False
    is_hard_disqualify: bool = False
    label: str = ""           # human-readable, surfaced on the brief (decision #4)
    detail: dict = field(default_factory=dict)


def make_signal(
    signal_type: str,
    lead: RawLead,
    *,
    label: str = "",
    detail: Optional[dict] = None,
) -> Signal:
    """Build a POSITIVE signal from a seeded spec. Detectors stay declarative —
    they pick the ``signal_type``; the prior, family, decay, and woe come from
    :mod:`score.economics`."""
    spec = economics.positive_spec(signal_type)
    return Signal(
        entity_key=entity_key_for(lead),
        signal_type=signal_type,
        source=lead.source,
        source_family=spec["family"],
        signal_class=spec["signal_class"],
        precision_prior=spec["prior"],
        # A POSITIVE signal never carries negative evidence — a prior at/below the
        # base rate is merely non-informative (woe 0), not proof the lead is bad.
        # Negative evidence comes exclusively from make_negative_signal. This also
        # keeps an "inert" spec (e.g. review_drop, prior 0.0) from acting as a
        # near-hard veto via a clamped −WOE_CLAMP.
        woe=max(0.0, woe_for_precision(spec["prior"])),
        date_event=lead.date_issued,
        lead_time_days=int(spec["lead_time_days"]),
        half_life_days=float(spec["half_life_days"]),
        leading=bool(spec["leading"]),
        is_negative=False,
        is_hard_disqualify=False,
        label=label or signal_type.replace("_", " "),
        detail=detail or {},
    )


def make_negative_signal(
    signal_type: str,
    lead: RawLead,
    *,
    label: str = "",
    detail: Optional[dict] = None,
) -> Signal:
    """Build a NEGATIVE signal. HARD types (demolition/revoked/closed/bankruptcy/
    in-house-hire) set ``is_hard_disqualify`` and veto the entity; SOFT types
    carry a fixed negative woe summed into the fusion (decision #3)."""
    spec = economics.NEGATIVE_SIGNAL_SPECS.get(signal_type, {"hard": False, "negative_woe": 0.0, "signal_class": "unknown"})
    hard = bool(spec["hard"])
    return Signal(
        entity_key=entity_key_for(lead),
        signal_type=signal_type,
        source=lead.source,
        source_family="negative",
        signal_class=spec["signal_class"],
        precision_prior=0.0,
        woe=0.0 if hard else float(spec["negative_woe"]),
        date_event=lead.date_issued,
        lead_time_days=0,
        half_life_days=30.0,
        leading=False,
        is_negative=True,
        is_hard_disqualify=hard,
        label=label or signal_type.replace("_", " "),
        detail=detail or {},
    )


# ---------------------------------------------------------------------------
# Decay — signal age is not decision proximity
# ---------------------------------------------------------------------------
# Minimum relevance a leading signal carries on its event day, so a day-0 permit
# can still corroborate (its lone confidence stays below the floor regardless).
_MIN_RAMP: float = 0.05


def decay_relevance(signal: Signal, as_of: date) -> float:
    """Relevance factor r ∈ [0,1] that multiplies a POSITIVE signal's woe.

    * Coincident (``leading=False``): exponential decay from ``date_event``,
      ``r = 0.5^(days_old/half_life)``, hard-expiring at 4× half-life.
    * Leading (``leading=True``): RAMP then decay — a brand-new permit is too
      early. ``r = days_old/peak`` while ``days_old < peak`` (= lead_time_days),
      then ``0.5^((days_old−peak)/half_life)``, hard-expiring at peak + 6× hl.

    Missing date → 1.0 (can't time it; don't punish). Negative signals are never
    passed here (their woe is undecayed by construction)."""
    if signal.date_event is None:
        return 1.0
    days_old = max(0, (as_of - signal.date_event).days)
    hl = max(1.0, signal.half_life_days)

    if not signal.leading:
        if days_old >= 4.0 * hl:
            return 0.0
        return 0.5 ** (days_old / hl)

    peak = max(1, signal.lead_time_days)
    if days_old < peak:
        # Ramp up, but never to a literal 0 on the event day — a brand-new
        # leading permit is too early to surface ALONE (its lone prior is below
        # the floor regardless), yet it should still be able to CORROBORATE a
        # coincident signal on day 0 rather than vanish from the family set.
        return max(_MIN_RAMP, days_old / peak)
    if days_old >= peak + 6.0 * hl:
        return 0.0
    return 0.5 ** ((days_old - peak) / hl)


def effective_woe(signal: Signal, as_of: date) -> float:
    """``woe_eff = r·max(woe,0) + min(woe,0)`` — decay the positive part only, so
    a contradiction keeps full bite as supporting evidence ages."""
    r = decay_relevance(signal, as_of)
    return r * max(signal.woe, 0.0) + min(signal.woe, 0.0)


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------
@dataclass
class FusionResult:
    """The fused verdict for one entity."""

    p_fused: float
    signal_class: str
    corroboration_count: int          # number of independent positive families
    n_signals: int
    is_disqualified: bool
    families: tuple[str, ...]
    evidence: list[str]               # human strings for the brief (decision #4)

    @property
    def signal_component(self) -> float:
        """The 0-10 value that occupies the engine's ``signal_class`` slot."""
        return round(10.0 * self.p_fused, 4)


# At most this many independent positive families contribute, so a freak stack
# of correlated sources can't run confidence to certainty (risk mitigation).
_MAX_FAMILIES = 3

# Intent priority used only as a deterministic tiebreaker when two positive
# signals carry identical woe but different classes (lower = preferred).
_CLASS_RANK: dict[str, int] = {
    "new_opening": 0,
    "churn_intent": 1,
    "expansion": 2,
    "unknown": 3,
}


def fuse(signals: list[Signal], as_of: date) -> FusionResult:
    """Combine all signals on one entity into one calibrated probability."""
    if not signals:
        return FusionResult(
            p_fused=economics.BASE_RATE,
            signal_class="unknown",
            corroboration_count=0,
            n_signals=0,
            is_disqualified=False,
            families=(),
            evidence=[],
        )

    # 1. Hard veto — any unambiguous death signal drops the entity (decision #3).
    if any(s.is_hard_disqualify for s in signals):
        return FusionResult(
            p_fused=0.0,
            signal_class="disqualified",
            corroboration_count=0,
            n_signals=len(signals),
            is_disqualified=True,
            families=(),
            evidence=[],
        )

    # 2. Per family: MAX positive woe_eff + SUM negative woe_eff.
    pos_by_family: dict[str, float] = {}
    neg_by_family: dict[str, float] = {}
    for s in signals:
        we = effective_woe(s, as_of)
        if s.is_negative or s.woe < 0:
            neg_by_family[s.source_family] = neg_by_family.get(s.source_family, 0.0) + we
        elif we > pos_by_family.get(s.source_family, 0.0):
            pos_by_family[s.source_family] = we

    # 3. Keep only the top-N positive families (cap manufactured corroboration).
    top_pos = dict(sorted(pos_by_family.items(), key=lambda kv: -kv[1])[:_MAX_FAMILIES])

    family_woes: dict[str, float] = {}
    for fam in set(top_pos) | set(neg_by_family):
        fw = top_pos.get(fam, 0.0) + neg_by_family.get(fam, 0.0)
        family_woes[fam] = _clamp(fw, -economics.WOE_CLAMP, economics.WOE_CLAMP)

    logit_fused = economics.BASE_LOGIT + sum(family_woes.values())
    p_fused = _clamp(sigmoid(logit_fused), 0.0, economics.P_FUSED_CAP)

    # 4. Dominant class = strongest positive signal; corroboration = # positive
    #    families that actually contributed.
    positives = [s for s in signals if not s.is_negative and s.woe >= 0]
    if positives:
        # Deterministic dominant pick: strongest woe, then by intent rank
        # (new_opening > churn > expansion > unknown), then signal_type — so an
        # equal-woe tie across classes never flips run-to-run on input order.
        dominant = max(
            positives,
            key=lambda s: (s.woe, -_CLASS_RANK.get(s.signal_class, 9), s.signal_type),
        )
        dominant_class = dominant.signal_class
    else:
        dominant_class = "unknown"

    corroboration = len(top_pos)
    evidence = _evidence_strings(positives)

    return FusionResult(
        p_fused=round(p_fused, 4),
        signal_class=dominant_class,
        corroboration_count=corroboration,
        n_signals=len(signals),
        is_disqualified=False,
        families=tuple(sorted(top_pos)),
        evidence=evidence,
    )


def _evidence_strings(positives: list[Signal]) -> list[str]:
    """Plain-words corroboration for the brief (decision #4) — strongest first,
    de-duplicated by label."""
    seen: set[str] = set()
    out: list[str] = []
    for s in sorted(positives, key=lambda s: -s.woe):
        lbl = s.label.strip()
        if lbl and lbl not in seen:
            seen.add(lbl)
            out.append(lbl)
    return out
