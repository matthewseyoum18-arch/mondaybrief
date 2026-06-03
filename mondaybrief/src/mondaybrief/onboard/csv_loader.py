"""Canonical CSV -> customers parsing logic.

Pure functions lifted out of the Streamlit upload page so they're importable
from both the UI layer and unit tests. No Streamlit, no DB, no side effects
beyond pandas operations on the input frame.

The contract mirrors ``mondaybrief/fixtures/ek_customers.csv`` minus the
``client_id`` column (the UI injects that from the session). Required columns
match the v1 cleaner plan: name, address, category, monthly_revenue_estimate,
with lat/lng optional (pipeline will geocode if missing).
"""
from __future__ import annotations

from io import StringIO
from typing import Optional

import pandas as pd


# Canonical column names we write to the ``customers`` table.
REQUIRED_COLS: list[str] = ["name", "address", "category", "monthly_revenue_estimate"]
OPTIONAL_COLS: list[str] = ["city", "state", "lat", "lng", "sqft", "status"]

# Map alternate header spellings to canonical names. Keeps the cleaner-side
# CSV forgiving -- "Monthly Rev" or "monthly_rev" both land on the right col.
ALIASES: dict[str, str] = {
    "monthly_revenue_estimate": "monthly_revenue_estimate",
    "monthly_rev": "monthly_revenue_estimate",
    "monthly_revenue": "monthly_revenue_estimate",
    "monthly_rev_estimate": "monthly_revenue_estimate",
    "lat": "lat",
    "latitude": "lat",
    "lng": "lng",
    "lon": "lng",
    "long": "lng",
    "longitude": "lng",
    "name": "name",
    "business_name": "name",
    "customer_name": "name",
    "address": "address",
    "street_address": "address",
    "city": "city",
    "state": "state",
    "category": "category",
    "vertical": "category",
    "sqft": "sqft",
    "square_feet": "sqft",
    "status": "status",
}


def normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase, snake_case, and alias-map the dataframe's columns.

    Also drops any ``client_id`` column shipped in the CSV -- the UI sets
    that from the authenticated session, never from user input.
    """
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    df = df.rename(columns=ALIASES)
    if "client_id" in df.columns:
        df = df.drop(columns=["client_id"])
    return df


def validate(df: pd.DataFrame) -> tuple[list[str], pd.DataFrame]:
    """Validate required columns and row-level required fields.

    Returns ``(errors, bad_rows)``:

    - ``errors`` is a list of human-readable validation messages.
    - ``bad_rows`` is a DataFrame of rows that should be skipped on save
      (empty required fields or non-numeric monthly_revenue_estimate).

    Side effect: coerces ``monthly_revenue_estimate`` to numeric in-place on
    the input frame (NaN where coercion fails). This mirrors the original
    behavior so callers see a clean numeric column afterwards.
    """
    errors: list[str] = []

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        errors.append(f"Missing required columns: {', '.join(missing)}")
        return errors, pd.DataFrame()

    bad_mask = (
        df["name"].isna()
        | df["address"].isna()
        | df["category"].isna()
        | df["monthly_revenue_estimate"].isna()
    )

    # monthly_revenue_estimate must be numeric.
    df["monthly_revenue_estimate"] = pd.to_numeric(
        df["monthly_revenue_estimate"], errors="coerce"
    )
    bad_mask = bad_mask | df["monthly_revenue_estimate"].isna()

    bad_rows = df[bad_mask]
    if len(bad_rows):
        errors.append(
            f"{len(bad_rows)} row(s) have empty required fields or non-numeric "
            f"monthly_revenue_estimate. They will be skipped on save."
        )

    return errors, bad_rows


def coerce_numeric(value, default: Optional[float] = None) -> Optional[float]:
    """Coerce a scalar to float, returning ``default`` on NaN/garbage."""
    if pd.isna(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_csv_bytes(raw: bytes) -> tuple[pd.DataFrame, list[str], pd.DataFrame]:
    """End-to-end parse: raw CSV bytes -> (df, errors, bad_rows).

    If the CSV itself fails to parse, the returned dataframe is empty and
    the parser error is the first message in ``errors``. Otherwise the
    dataframe has normalized headers and ``errors``/``bad_rows`` come from
    :func:`validate`.
    """
    try:
        df = pd.read_csv(StringIO(raw.decode("utf-8")))
    except Exception as exc:  # noqa: BLE001 - parser failures bubble as messages
        return pd.DataFrame(), [f"Could not parse CSV: {exc}"], pd.DataFrame()

    df = normalize_headers(df)
    errors, bad_rows = validate(df)
    return df, errors, bad_rows
