"""Per-client scoring profile — what makes a lead good for *this* cleaner.

A ``ClientProfile`` carries the four things that vary by cleaner:

  * ``weights``              — relative importance of each scoring component.
  * ``category_prefs``       — how much this cleaner wants each category (0-10).
  * ``min_contract_monthly`` — the smallest account worth their drive.
  * ``max_drive_minutes``    — how far off-route they'll still take work.
  * ``exclusions``           — categories they never want (e.g. restaurants).

The profile is *seeded from the customer book* at onboarding (zero friction —
works on the very first brief) and *tuned from thumbs feedback* over time.
Tuning uses shrinkage toward the seeded values so a cleaner with only a handful
of feedback labels doesn't get a wildly overfit profile: with N labels the
observed signal is trusted with weight ``lambda = N / (N + 50)`` and the seed
holds the rest. At N=0 lambda=0 (all seed); at N=50 lambda=0.5; lambda -> 1 as
labels accumulate. Cold-start is safe because the seed itself is the prior.

This is the per-client half of the scoring system. The deterministic component
math lives in :mod:`score.engine`; the researched constants in
:mod:`score.economics`.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..models import Customer
from . import economics


class ClientProfile(BaseModel):
    """Tunable per-client scoring configuration. One row per client."""

    client_id: str
    weights: dict[str, float] = Field(
        default_factory=lambda: dict(economics.GLOBAL_DEFAULT_WEIGHTS)
    )
    category_prefs: dict[str, float] = Field(
        default_factory=lambda: dict(economics.CATEGORY_DESIRABILITY)
    )
    min_contract_monthly: float = 0.0
    max_drive_minutes: float = 15.0
    exclusions: list[str] = Field(default_factory=list)

    def preference_for(self, category: str | None) -> float:
        """Category desirability 0-10 for this client, falling back to the
        global default then 'other'."""
        cat = economics.canonical_category(category)
        if cat in self.category_prefs:
            return self.category_prefs[cat]
        return economics.CATEGORY_DESIRABILITY.get(cat, economics.CATEGORY_DESIRABILITY["other"])

    def excludes(self, category: str | None) -> bool:
        return economics.canonical_category(category) in {
            economics.canonical_category(c) for c in self.exclusions
        }


def _book_category_counts(customers: list[Customer]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in customers:
        cat = economics.canonical_category(c.category)
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def seed_from_book(
    client_id: str,
    customers: list[Customer],
    *,
    default_drive_minutes: float = 15.0,
) -> ClientProfile:
    """Derive a starting profile from the cleaner's existing customer book.

    * ``category_prefs`` — blend the global desirability tier with what the
      cleaner *actually* serves: a category they already clean a lot gets a
      bump (revealed preference), unserved categories a mild discount.
    * ``min_contract_monthly`` — left at 0 (off) by default. A hard dollar floor
      auto-derived from billed revenue misfires against the engine's
      conservative sqft-based contract estimate (different scales), so we don't
      auto-seed it. The margin component already gates small accounts on the
      same estimation scale. The floor stays an explicit opt-in knob a cleaner
      can set ("don't show me anything under $X").
    * ``max_drive_minutes`` — the configured service-area isochrone.

    With an empty book we fall back entirely to global defaults.
    """
    if not customers:
        return ClientProfile(
            client_id=client_id,
            min_contract_monthly=0.0,
            max_drive_minutes=default_drive_minutes,
        )

    counts = _book_category_counts(customers)
    total = sum(counts.values()) or 1

    # Revealed preference: start every category at a mild discount of its global
    # desirability ("this isn't what I do"), then boost the categories this
    # cleaner actually serves by up to +2.5 based on book share (capped at 10).
    # Net effect: an office-heavy book ranks office leads well above restaurants,
    # and a restaurant-heavy book does the reverse — so the SAME lead scores
    # differently per client even before any feedback. Feedback tuning can later
    # raise a discounted category if the cleaner thumbs-up leads in it.
    UNSERVED_DISCOUNT = 0.8
    prefs: dict[str, float] = {
        cat: round(val * UNSERVED_DISCOUNT, 2)
        for cat, val in economics.CATEGORY_DESIRABILITY.items()
    }
    for cat, n in counts.items():
        share = n / total
        base = economics.CATEGORY_DESIRABILITY.get(cat, economics.CATEGORY_DESIRABILITY["other"])
        prefs[cat] = round(min(10.0, base + min(2.5, share * 8.0)), 2)

    return ClientProfile(
        client_id=client_id,
        category_prefs=prefs,
        min_contract_monthly=0.0,  # opt-in; see docstring
        max_drive_minutes=default_drive_minutes,
    )


def shrinkage_lambda(n_labels: int, *, prior_strength: float = 50.0) -> float:
    """Weight placed on OBSERVED feedback vs the seeded prior.

    ``lambda = N / (N + prior_strength)`` — the James-Stein / Bayesian shrinkage
    form. It is 0 at N=0 (trust the seed entirely), 0.5 at N=prior_strength, and
    approaches 1 as feedback accumulates (trust the data). This keeps a cleaner
    with only a handful of labels close to the seeded prior instead of
    overfitting to noise.
    """
    n = max(0, int(n_labels))
    return n / (n + prior_strength)


def tune_from_feedback(
    profile: ClientProfile,
    feedback_rows: list[tuple[str, str]],
    *,
    prior_strength: float = 50.0,
) -> ClientProfile:
    """Return a profile with ``category_prefs`` shrunk toward observed like-rate.

    ``feedback_rows`` is ``[(category, thumbs)]`` where thumbs is 'up'/'down'.
    For each category with feedback:

        observed = like_rate * 10            # 0-10 on the same scale as prefs
        pref'    = (1 - lambda) * seed + lambda * observed   # lambda = N/(N+50)

    where lambda comes from that category's label count. Categories without
    feedback keep their seeded value. v1 tunes category_prefs only; weight-
    vector learning (logistic regression) is the documented v1.1 upgrade and is
    intentionally not done here (too little data per client to be robust).
    """
    if not feedback_rows:
        return profile

    agg: dict[str, list[int]] = {}
    for category, thumbs in feedback_rows:
        cat = economics.canonical_category(category)
        agg.setdefault(cat, []).append(1 if thumbs == "up" else 0)

    new_prefs = dict(profile.category_prefs)
    for cat, votes in agg.items():
        n = len(votes)
        if n == 0:
            continue
        like_rate = sum(votes) / n
        observed = like_rate * 10.0
        seed = profile.category_prefs.get(
            cat, economics.CATEGORY_DESIRABILITY.get(cat, economics.CATEGORY_DESIRABILITY["other"])
        )
        lam = shrinkage_lambda(n, prior_strength=prior_strength)
        new_prefs[cat] = round((1.0 - lam) * seed + lam * observed, 2)

    return profile.model_copy(update={"category_prefs": new_prefs})
