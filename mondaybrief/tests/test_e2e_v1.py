"""Unit tests for the new e2e v1 modules.

These run offline — no network, no DB. Anything that would touch psycopg,
postmarker, or stripe is stubbed via monkeypatch. We keep each test scoped
to one module so a failure points straight at the culprit.
"""
from __future__ import annotations

import importlib
import urllib.parse

import pytest


# ---------------------------------------------------------------------------
# csv_loader (created by a parallel agent — may not exist yet)
# ---------------------------------------------------------------------------

csv_loader = None
try:
    csv_loader = importlib.import_module("mondaybrief.onboard.csv_loader")
except ImportError:
    csv_loader = None


@pytest.mark.skipif(
    csv_loader is None,
    reason="mondaybrief.onboard.csv_loader not yet implemented by parallel agent",
)
def test_csv_loader_normalizes_aliases() -> None:
    """Friendly header names should be remapped to canonical column names."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "Business Name": ["Foo Cafe"],
            "Monthly Rev": [1500],
            "Latitude": [41.9],
        }
    )
    normalized = csv_loader.normalize_headers(df)
    cols = set(normalized.columns)
    assert "name" in cols
    assert "monthly_revenue_estimate" in cols
    assert "lat" in cols


@pytest.mark.skipif(
    csv_loader is None,
    reason="mondaybrief.onboard.csv_loader not yet implemented by parallel agent",
)
def test_csv_loader_validate_missing_required() -> None:
    """validate() should flag missing required columns in its error list."""
    import pandas as pd

    df = pd.DataFrame({"name": ["Foo"], "category": ["office"]})
    errors = csv_loader.validate(df)
    joined = " ".join(errors) if isinstance(errors, list) else str(errors)
    assert "Missing required columns" in joined


@pytest.mark.skipif(
    csv_loader is None,
    reason="mondaybrief.onboard.csv_loader not yet implemented by parallel agent",
)
def test_csv_loader_parse_csv_bytes_happy() -> None:
    """A well-formed CSV byte blob parses into a 1-row df with no errors."""
    payload = b"name,address,category,monthly_revenue_estimate\nFoo,123 St,office,1500\n"
    result = csv_loader.parse_csv_bytes(payload)

    # parse_csv_bytes returns (df, errors, bad_rows); support an attr-style obj too.
    if isinstance(result, tuple):
        df, errors = result[0], result[1]
    else:
        df, errors = result.df, result.errors

    assert len(df) == 1
    assert not errors


# ---------------------------------------------------------------------------
# feedback.tokens
# ---------------------------------------------------------------------------


def test_feedback_token_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """A freshly minted token decodes back to the same payload."""
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", "test-feedback-secret-32-bytes-long-xx")
    from mondaybrief.feedback import tokens

    token = tokens.generate_feedback_token(42, "client-uuid")
    payload = tokens.verify_feedback_token(token)

    assert payload is not None
    assert payload["scored_lead_id"] == 42
    assert payload["client_id"] == "client-uuid"


def test_feedback_token_tamper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flipping the last character invalidates the HMAC signature."""
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", "test-feedback-secret-32-bytes-long-xx")
    from mondaybrief.feedback import tokens

    token = tokens.generate_feedback_token(42, "client-uuid")
    # Mutate the last char to something guaranteed-different.
    last = token[-1]
    swap = "A" if last != "A" else "B"
    tampered = token[:-1] + swap

    assert tokens.verify_feedback_token(tampered) is None


# ---------------------------------------------------------------------------
# auth.magic_link
# ---------------------------------------------------------------------------


def test_magic_link_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """generate_link → extract token from URL → verify_link recovers client_id."""
    monkeypatch.setenv("MAGIC_LINK_SECRET", "test-magic-link-secret-32-bytes-long")
    from mondaybrief.auth import magic_link

    url = magic_link.generate_link("uuid-x", "x@y.com")
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    assert "token" in query
    token = query["token"][0]

    assert magic_link.verify_link(token) == "uuid-x"


# ---------------------------------------------------------------------------
# observability.cost
# ---------------------------------------------------------------------------


def test_cost_geocodio_pricing() -> None:
    """Per-call unit pricing math is what the comments claim."""
    from mondaybrief.observability import cost

    # 100 lookups × $0.001 = $0.10
    assert cost.geocodio_cost(100) == pytest.approx(0.10)
    # 10 lookups × $0.008 = $0.08
    assert cost.twilio_cost(10) == pytest.approx(0.08)
    # Mapbox is free-tier — always 0.
    assert cost.mapbox_cost(5) == 0


# ---------------------------------------------------------------------------
# billing.dunning
# ---------------------------------------------------------------------------


def test_dunning_subject_escalation() -> None:
    """Subject line escalates from gentle (attempt 1) to final-notice (attempt 3+)."""
    from mondaybrief.billing.dunning import _subject

    assert "didn't go through" in _subject(1)
    assert "Final notice" in _subject(3)
