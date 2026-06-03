"""Unit tests for the per-client profile: seeding from the book and shrinkage
tuning from feedback."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mondaybrief.models import Customer  # noqa: E402
from mondaybrief.score import economics  # noqa: E402
from mondaybrief.score.profile import (  # noqa: E402
    seed_from_book,
    shrinkage_lambda,
    tune_from_feedback,
)


def _cust(category, rev=1500):
    return Customer(
        client_id="c", name=f"{category} acct", address="1 St", city="Chicago",
        state="IL", category=category, monthly_rev=rev,
    )


def test_seed_boosts_served_and_discounts_unserved():
    office_book = [_cust("office"), _cust("professional")]
    p = seed_from_book("office_co", office_book)
    # Served category boosted above its global tier; unserved discounted below.
    assert p.preference_for("office") > economics.CATEGORY_DESIRABILITY["office"]
    assert p.preference_for("restaurant") < economics.CATEGORY_DESIRABILITY["restaurant"]


def test_two_books_yield_opposite_preferences():
    office_p = seed_from_book("o", [_cust("office"), _cust("professional")])
    resto_p = seed_from_book("r", [_cust("restaurant"), _cust("cafe")])
    assert office_p.preference_for("office") > resto_p.preference_for("office")
    assert resto_p.preference_for("restaurant") > office_p.preference_for("restaurant")


def test_empty_book_uses_global_defaults():
    p = seed_from_book("new", [])
    assert p.weights == economics.GLOBAL_DEFAULT_WEIGHTS
    assert p.category_prefs == economics.CATEGORY_DESIRABILITY
    assert p.min_contract_monthly == 0.0


def test_contract_floor_not_auto_seeded():
    # Auto-seeding a dollar floor misfires against the engine's estimate scale,
    # so seeding leaves it off (0) by design.
    p = seed_from_book("o", [_cust("office", rev=2200), _cust("office", rev=1800)])
    assert p.min_contract_monthly == 0.0


def test_shrinkage_lambda_grows_with_labels():
    # lambda = weight on observed = N/(N+50): rises with N.
    assert shrinkage_lambda(0) < shrinkage_lambda(50) < shrinkage_lambda(500)
    assert shrinkage_lambda(0) == 0.0  # no feedback -> trust the seed entirely
    assert abs(shrinkage_lambda(50) - 0.5) < 1e-9  # N == prior_strength -> 0.5


def test_tune_small_n_stays_near_seed():
    p = seed_from_book("o", [_cust("office")])
    seed_pref = p.preference_for("restaurant")
    # 2 thumbs-up on restaurant — tiny N => shrink hard toward the (low) seed.
    tuned = tune_from_feedback(p, [("restaurant", "up"), ("restaurant", "up")])
    moved = tuned.preference_for("restaurant") - seed_pref
    # It moves up (toward observed 10) but only a little at N=2.
    assert 0 < moved < 1.0


def test_tune_large_n_moves_toward_observed():
    p = seed_from_book("o", [_cust("office")])
    seed_pref = p.preference_for("restaurant")
    # 100 thumbs-up on restaurant => trust the observed like-rate (10) more.
    rows = [("restaurant", "up")] * 100
    tuned = tune_from_feedback(p, rows)
    assert tuned.preference_for("restaurant") > seed_pref + 1.5


def test_tune_down_votes_lower_preference():
    p = seed_from_book("o", [_cust("office"), _cust("office"), _cust("office")])
    seed_pref = p.preference_for("office")  # boosted high (served)
    rows = [("office", "down")] * 60  # they keep skipping office leads
    tuned = tune_from_feedback(p, rows)
    assert tuned.preference_for("office") < seed_pref


def test_tune_no_rows_is_identity():
    p = seed_from_book("o", [_cust("office")])
    assert tune_from_feedback(p, []).category_prefs == p.category_prefs
