"""Researched commercial-cleaning economics — the single source of truth for
the deterministic scoring engine's numbers.

Every constant here is grounded in 2026 industry pricing/operations research so
the component sub-scores are *defensible*, not invented. Keeping them in one
module means a reviewer can audit "why is a medical office worth more than a
cafe" in one place, and a future calibration pass can tune the numbers without
hunting through engine logic.

Sources (per-figure citations inline):
  - ISSA commercial cleaning rates per square foot
      https://www.issa.com/articles/commercial-cleaning-rates-per-square-foot/
  - Housecall Pro 2026 commercial pricing guide
      https://www.housecallpro.com/resources/how-to-price-commercial-cleaning-jobs/
  - Method Clean Biz — price per sqft for profit
      https://methodcleanbiz.com/2025/02/17/learn-to-price-commercial-cleaning-per-sq-ft-for-profit/
  - Financial Models Lab — route-density profitability
      https://financialmodelslab.com/blogs/profitability/commercial-cleaning
  - Cleaning Business Academy — route density strategies
      https://www.cleaningbusinessacademy.com/route-density-strategies-cleaning-profits/
  - Ziva Cleaning — medical office cleaning cost
      https://zivacleaning.com/blog/medical-office-cleaning-cost
"""
from __future__ import annotations

import math


# ---------------------------------------------------------------------------
# Canonical categories. These mirror the values emitted by score.taxonomy and
# the ScoredLead.category description. Anything outside this set maps to
# "other" before lookup.
# ---------------------------------------------------------------------------
CANONICAL_CATEGORIES: tuple[str, ...] = (
    "medical office",
    "dental clinic",
    "vet clinic",
    "professional",
    "office",
    "restaurant",
    "cafe",
    "retail",
    "fitness studio",
    "gym",
    "school",
    "other",
)


# ---------------------------------------------------------------------------
# $ / cleanable sqft / month, by category. Mid-point of the researched band.
# ISSA + Housecall Pro + Ziva. Medical/dental carry a 25-50% premium for
# OSHA/terminal-clean protocols; restaurants price high for overnight labor
# intensity; schools/retail sit at the low end.
# ---------------------------------------------------------------------------
DOLLARS_PER_SQFT_MONTH: dict[str, float] = {
    "medical office": 0.275,   # 0.20-0.35
    "dental clinic":  0.275,   # 0.20-0.35
    "vet clinic":     0.24,    # vet ~ light-medical
    "professional":   0.13,    # 0.09-0.17
    "office":         0.13,    # 0.09-0.17
    "restaurant":     0.30,    # 0.25-0.35+
    "cafe":           0.22,    # lighter than full restaurant
    "retail":         0.11,    # 0.07-0.15
    "fitness studio": 0.165,   # 0.11-0.22
    "gym":            0.165,   # 0.11-0.22
    "school":         0.105,   # 0.07-0.14
    "other":          0.12,    # generic commercial default
}

# Net margin fraction the cleaner keeps after labor/supplies/overhead.
# Industry target 10-30%; recurring nightly contracts cluster ~25%.
NET_MARGIN_FRACTION: dict[str, float] = {
    "medical office": 0.30,
    "dental clinic":  0.30,
    "vet clinic":     0.28,
    "professional":   0.25,
    "office":         0.25,
    "restaurant":     0.20,   # high labor intensity erodes margin
    "cafe":           0.22,
    "retail":         0.22,
    "fitness studio": 0.22,
    "gym":            0.22,
    "school":         0.18,   # budget-constrained, slow-pay
    "other":          0.22,
}

# Fallback cleanable sqft when a permit/license row carries none. Median for
# a small-commercial unit of that category.
MEDIAN_SQFT: dict[str, int] = {
    "medical office": 4000,
    "dental clinic":  3000,
    "vet clinic":     3500,
    "professional":   5000,
    "office":         6000,
    "restaurant":     3500,
    "cafe":           1500,
    "retail":         4000,
    "fitness studio": 5000,
    "gym":            8000,
    "school":         20000,
    "other":          4000,
}


# ---------------------------------------------------------------------------
# Margin sub-score banding: estimated NET monthly margin ($) -> 0-10.
# `estimated_monthly_margin` returns net margin (contract value × net fraction),
# so these floors are net-margin dollars, NOT contract value. Anchored to the
# research worked example (~$337 net for a decent office → ~7). Fixed, not
# batch-relative, so "7" means the same take-home every week.
# (band_floor_usd, score) sorted descending; first floor <= value wins.
# ---------------------------------------------------------------------------
MARGIN_BANDS: tuple[tuple[float, float], ...] = (
    (600.0, 10.0),
    (400.0, 8.5),
    (275.0, 7.0),
    (180.0, 5.5),
    (110.0, 4.0),
    (50.0, 2.5),
    (0.0, 1.0),
)


# ---------------------------------------------------------------------------
# Route sub-score: drive-minutes off existing route -> 0-10.
# Financial Models Lab / Cleaning Business Academy: dense routes (<15 min)
# protect margin; >30 min erodes it; decline >60 min unless contract is large.
# (max_minutes_inclusive, score) sorted ascending; first max >= value wins.
# ---------------------------------------------------------------------------
ROUTE_BANDS: tuple[tuple[float, float], ...] = (
    (5.0, 10.0),
    (10.0, 8.0),
    (15.0, 7.0),
    (20.0, 6.0),
    (30.0, 5.0),
    (45.0, 3.5),
    (60.0, 2.0),
    (float("inf"), 0.5),
)

# City-driving proxy when no routing API is available: minutes per km.
# ~2 min/km matches a Chicago/NYC grid at off-peak nightly-service hours.
DRIVE_MINUTES_PER_KM: float = 2.0


# ---------------------------------------------------------------------------
# Category desirability tier (0-10). Global default the per-client
# category_prefs are seeded from + blended toward. Reflects margin %, payment
# reliability, and retention from the research desirability ranking.
# ---------------------------------------------------------------------------
CATEGORY_DESIRABILITY: dict[str, float] = {
    "medical office": 8.5,
    "dental clinic":  8.5,
    "vet clinic":     7.5,
    "professional":   7.0,
    "office":         7.0,
    "restaurant":     5.5,   # high $/sqft but payment + labor risk
    "cafe":           5.5,
    "retail":         6.0,
    "fitness studio": 5.0,
    "gym":            5.0,
    "school":         4.0,   # slow-pay government, seasonal
    "other":          3.0,
}


# ---------------------------------------------------------------------------
# Signal-class intent strength (0-10). New openings have an open vendor slot
# NOW; churn buyers are actively shopping; expansion already has an incumbent.
# Replaces the old multiplicative SIGNAL_CLASS_MULTIPLIER — now an additive,
# weighted component so it can't silently swamp the other signals.
# ---------------------------------------------------------------------------
SIGNAL_CLASS_STRENGTH: dict[str, float] = {
    "new_opening":  10.0,
    "churn_intent": 9.0,
    "expansion":    7.0,
    "unknown":      5.0,
    "disqualified": 0.0,
}


# ---------------------------------------------------------------------------
# Timing freshness decay. score = 10 * 0.5 ** (days_old / HALF_LIFE_DAYS).
# A trigger filed today scores 10; ~3 weeks old scores ~5.
# ---------------------------------------------------------------------------
TIMING_HALF_LIFE_DAYS: float = 21.0


# ---------------------------------------------------------------------------
# Default global component weights. Need not sum to 1 (engine normalizes by
# their sum). Per-client weights start here and shrink toward observed
# feedback over time.
# ---------------------------------------------------------------------------
GLOBAL_DEFAULT_WEIGHTS: dict[str, float] = {
    "margin":       0.30,
    "route":        0.25,
    "category":     0.20,
    "timing":       0.10,
    "signal_class": 0.15,
}

# Soft risk penalties (subtracted from the normalized [0,1] score).
RISK_EXCLUDED_CATEGORY: float = 0.25
RISK_BELOW_CONTRACT_FLOOR: float = 0.15
RISK_UNION_EXCLUDED: float = 0.20

# Tier thresholds on the final 0-100 score. Fixed — tune weights, not these.
TIER_A_MIN: int = 70
TIER_B_MIN: int = 45
TIER_C_MIN: int = 30


def canonical_category(category: str | None) -> str:
    """Map any category string to a known canonical key, defaulting to 'other'."""
    if not category:
        return "other"
    key = category.strip().lower()
    return key if key in DOLLARS_PER_SQFT_MONTH else "other"


def estimated_contract_value(category: str | None, sqft: int | float | None) -> float:
    """Estimated GROSS monthly contract value ($) — what the account would bill.

    contract = sqft * $psf[category]. Falls back to category median sqft when
    sqft is missing. Used for the per-client contract-floor gate (which is
    seeded from the cleaner's actual monthly_rev — also a gross figure — so the
    comparison is gross-to-gross).
    """
    cat = canonical_category(category)
    eff_sqft = float(sqft) if sqft else float(MEDIAN_SQFT[cat])
    return round(eff_sqft * DOLLARS_PER_SQFT_MONTH[cat], 2)


def estimated_monthly_margin(category: str | None, sqft: int | float | None) -> float:
    """Estimated NET monthly margin ($) the cleaner keeps if they win this
    account: contract_value * net_margin_fraction[category]. Used for the margin
    sub-score band (see MARGIN_BANDS, which are net-margin dollars)."""
    cat = canonical_category(category)
    return round(estimated_contract_value(category, sqft) * NET_MARGIN_FRACTION[cat], 2)


# ===========================================================================
# v2 SIGNAL FUSION LAYER  (2026-06-02)
#
# The naive v1 signal layer gave one record one coarse class and one fixed
# strength. v2 turns every public record into one or more calibrated Signals,
# FUSES the signals that land on the same business (name+address) into one
# probability via Naive-Bayes / weight-of-evidence log-odds, and suppresses
# anything below a confidence floor. These constants are the single source of
# truth for that math; the logic lives in score/signal_layer.py and the
# detectors in score/detectors.py. Full design: vault note
# "MondayBrief Signal Fusion System v2".
# ===========================================================================

# Base rate: P(a raw open-data row is a genuine cleanable open-vendor prospect).
# The base-rate fallacy is the dominant false-positive driver in open data, so
# every signal's evidence is measured RELATIVE to this prior.
BASE_RATE: float = 0.03
BASE_LOGIT: float = math.log(BASE_RATE / (1.0 - BASE_RATE))  # ≈ -3.4761

# Per-family weight-of-evidence clamp. Generous on purpose: it guards numeric
# extremes (precision near 0 or 1) WITHOUT distorting a lone strong signal, so a
# single signal fuses back to ~its own precision. (Research proposed 2.0, which
# capped every lone signal below ~0.19 and emptied briefs — see design note.)
WOE_CLAMP: float = 6.0

# Hard confidence floor (product decision #1): a lead whose fused probability is
# below this is suppressed from the brief entirely, regardless of how high its
# weighted 0-100 engine score is. Tunable; re-fit every 4 weeks toward observed
# precision@10. At 0.40 a genuinely strong coincident opener (license 0.72, food
# 0.85, CO 0.55) surfaces alone; a generic-office limited license (0.45) surfaces
# only while FRESH (decays below the floor within days, then needs corroboration);
# and weak leading signals (raw permit 0.35, unfiltered DCWP 0.18) require
# corroboration at any age.
CONFIDENCE_FLOOR: float = 0.40

# Ceiling so fused certainty never reaches a literal 1.0 (keeps log-odds finite
# and reflects irreducible open-data noise).
P_FUSED_CAP: float = 0.985

# Source families. Positive evidence takes the MAX within a family and SUMs
# across families, so correlated rows tracing to one underlying event (a
# building permit + its later certificate of occupancy on the same parcel)
# cannot manufacture confidence, while genuinely independent corroboration
# (a license + a permit + an RFP) compounds. 'reputation' is wired but inert —
# review/BBB scraping is ToS-blocked (see project_oss_no_go_list).
SIGNAL_FAMILIES: tuple[str, ...] = ("license", "construction", "market", "reputation")

# Default Beta pseudo-counts behind a seeded prior (k = alpha + beta). Smaller k
# = the prior moves faster as real thumbs feedback arrives; large enough that a
# few downvotes can't collapse a class. High-intent classes start at 20,
# skeptical classes (expansion/unknown) at 30.
PRIOR_PSEUDOCOUNT_DEFAULT: int = 20
PRIOR_PSEUDOCOUNT_SKEPTICAL: int = 30

# ---------------------------------------------------------------------------
# POSITIVE signal specs, keyed by the signal_type a detector emits. One source
# can emit several types (a Chicago license row is a strong cleanable-allowlist
# new_opening OR a weak unfiltered "Limited Business License"), so the key is the
# signal_type, not the dataset id. Fields:
#   prior          P(genuine open-vendor decision | this signal), the Beta mean
#   k              Beta pseudo-count (alpha+beta) behind the prior
#   family         corroboration family (see SIGNAL_FAMILIES)
#   signal_class   coarse class for narrative + back-compat
#   half_life_days decay half-life once inside the hot window
#   lead_time_days days from the signal's date_event to the vendor decision
#   leading        True -> ramp-then-decay relevance; False -> coincident decay
# ---------------------------------------------------------------------------
POSITIVE_SIGNAL_SPECS: dict[str, dict] = {
    # --- license family (the legal right to operate; mostly coincident) ----
    "chi_food_license":     {"prior": 0.88, "k": 20, "family": "license",      "signal_class": "new_opening",  "half_life_days": 14.0, "lead_time_days": 14,  "leading": False},
    "nyc_food_new":         {"prior": 0.85, "k": 20, "family": "license",      "signal_class": "new_opening",  "half_life_days": 14.0, "lead_time_days": 14,  "leading": False},
    "chi_liquor_new":       {"prior": 0.80, "k": 20, "family": "license",      "signal_class": "new_opening",  "half_life_days": 21.0, "lead_time_days": 30,  "leading": False},
    "chi_license_issue":    {"prior": 0.72, "k": 20, "family": "license",      "signal_class": "new_opening",  "half_life_days": 30.0, "lead_time_days": 14,  "leading": False},
    "nyc_dcwp_premise":     {"prior": 0.50, "k": 20, "family": "license",      "signal_class": "new_opening",  "half_life_days": 30.0, "lead_time_days": 30,  "leading": False},
    "chi_license_limited":  {"prior": 0.45, "k": 30, "family": "license",      "signal_class": "new_opening",  "half_life_days": 30.0, "lead_time_days": 14,  "leading": False},
    "nyc_dcwp_unfiltered":  {"prior": 0.18, "k": 30, "family": "license",      "signal_class": "new_opening",  "half_life_days": 30.0, "lead_time_days": 30,  "leading": False},
    # --- construction family (physical buildout/occupancy; mostly leading) -
    "nyc_co":               {"prior": 0.55, "k": 20, "family": "construction", "signal_class": "new_opening",  "half_life_days": 21.0, "lead_time_days": 14,  "leading": False},
    "chi_permit_sign":      {"prior": 0.45, "k": 20, "family": "construction", "signal_class": "new_opening",  "half_life_days": 30.0, "lead_time_days": 45,  "leading": True},
    "chi_permit_alteration":{"prior": 0.40, "k": 30, "family": "construction", "signal_class": "expansion",    "half_life_days": 75.0, "lead_time_days": 90,  "leading": True},
    "chi_permit_newcon":    {"prior": 0.35, "k": 30, "family": "construction", "signal_class": "new_opening",  "half_life_days": 90.0, "lead_time_days": 120, "leading": True},
    "nyc_dob_nb":           {"prior": 0.35, "k": 30, "family": "construction", "signal_class": "new_opening",  "half_life_days": 90.0, "lead_time_days": 120, "leading": True},
    "nyc_dob_alt":          {"prior": 0.35, "k": 30, "family": "construction", "signal_class": "expansion",    "half_life_days": 75.0, "lead_time_days": 90,  "leading": True},
    # --- market family (transactional intent) ------------------------------
    "rfp_passport":         {"prior": 0.80, "k": 20, "family": "market",       "signal_class": "churn_intent", "half_life_days": 7.0,  "lead_time_days": 0,   "leading": False},
    "rfp_demandstar":       {"prior": 0.55, "k": 20, "family": "market",       "signal_class": "churn_intent", "half_life_days": 14.0, "lead_time_days": 0,   "leading": False},
    "rfp_govt":             {"prior": 0.15, "k": 30, "family": "market",       "signal_class": "churn_intent", "half_life_days": 14.0, "lead_time_days": 0,   "leading": False},
    "parcel_sale":          {"prior": 0.30, "k": 30, "family": "market",       "signal_class": "expansion",    "half_life_days": 120.0,"lead_time_days": 180, "leading": True},
    "eviction_commercial":  {"prior": 0.10, "k": 30, "family": "market",       "signal_class": "expansion",    "half_life_days": 120.0,"lead_time_days": 180, "leading": True},
    # --- reputation family (INERT — ToS-blocked, prior 0.0 keeps it dead) ---
    "review_drop":          {"prior": 0.0,  "k": 30, "family": "reputation",   "signal_class": "churn_intent", "half_life_days": 14.0, "lead_time_days": 0,   "leading": False},
    # --- fallback ----------------------------------------------------------
    "unknown":              {"prior": 0.05, "k": 30, "family": "license",      "signal_class": "unknown",      "half_life_days": 21.0, "lead_time_days": 0,   "leading": False},
}

# ---------------------------------------------------------------------------
# NEGATIVE signal specs. HARD types short-circuit the whole entity to dropped
# (P_fused = 0), preserving the existing _is_disqualified behaviour. SOFT types
# contribute fixed negative weight-of-evidence into the log-odds sum (they
# down-weight without nuking a strongly corroborated lead) and are NEVER decayed
# — a kill/cooling signal keeps full bite as positive evidence ages.
#   hard          True -> force P_fused 0.0
#   negative_woe  fixed (<=0) WOE contribution for soft negatives
# ---------------------------------------------------------------------------
NEGATIVE_SIGNAL_SPECS: dict[str, dict] = {
    "demolition":       {"hard": True,  "negative_woe": 0.0,  "signal_class": "disqualified"},
    "license_revoked":  {"hard": True,  "negative_woe": 0.0,  "signal_class": "disqualified"},
    "closed":           {"hard": True,  "negative_woe": 0.0,  "signal_class": "disqualified"},
    "bankruptcy":       {"hard": True,  "negative_woe": 0.0,  "signal_class": "disqualified"},
    "inhouse_hire":     {"hard": True,  "negative_woe": 0.0,  "signal_class": "disqualified"},
    "ownership_change": {"hard": False, "negative_woe": -0.7, "signal_class": "churn_intent"},
    "expired_license":  {"hard": False, "negative_woe": -1.0, "signal_class": "unknown"},
    "lawsuit":          {"hard": False, "negative_woe": -0.6, "signal_class": "churn_intent"},
    "lien":             {"hard": False, "negative_woe": -0.5, "signal_class": "unknown"},
    "eviction_tenant":  {"hard": False, "negative_woe": -0.8, "signal_class": "unknown"},
}


def positive_spec(signal_type: str) -> dict:
    """Look up a positive signal spec, falling back to the 'unknown' spec."""
    return POSITIVE_SIGNAL_SPECS.get(signal_type, POSITIVE_SIGNAL_SPECS["unknown"])
