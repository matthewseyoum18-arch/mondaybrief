"""Upload Customers page — CSV in, ``customers`` rows out.

CSV contract mirrors ``mondaybrief/fixtures/ek_customers.csv`` minus the
``client_id`` column (we inject that from the session). Required columns
match the v1 plan: name, address, category, monthly_revenue_estimate, with
lat/lng optional (pipeline will geocode if missing).

We do NOT trust ``client_id`` from the CSV — even if the file contains one
it is discarded and replaced with the session's client UUID. Multi-tenant
safety beats convenience.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st

from ...db import connect
from ...onboard.csv_loader import (
    ALIASES,  # noqa: F401 - re-exported for back-compat
    OPTIONAL_COLS,  # noqa: F401 - re-exported for back-compat
    REQUIRED_COLS,  # noqa: F401 - re-exported for back-compat
    coerce_numeric,
    normalize_headers,
    parse_csv_bytes,
    validate,
)


# Back-compat private aliases for any external callers that imported the
# underscore-prefixed names from this module.
_normalize_headers = normalize_headers
_validate = validate
_coerce_numeric = coerce_numeric


def _save_to_db(df: pd.DataFrame, client_id: str) -> int:
    """Insert rows into ``customers`` keyed by the session ``client_id`` UUID.

    Returns the number of rows written. We use ON CONFLICT on
    ``(client_id, address)`` to make the upload idempotent — re-uploading
    the same book updates revenue/sqft instead of erroring.

    Note: the existing schema's TEXT ``client_id`` column is a slug. We need
    a slug for back-compat with non-migrated code, so we look it up from
    the ``clients`` table by the session UUID and use it alongside the FK.
    """
    # Drop bad rows before we write.
    df = df.dropna(subset=["name", "address", "category", "monthly_revenue_estimate"])
    if df.empty:
        return 0

    with connect() as conn:
        # Look up slug for back-compat TEXT client_id column.
        slug_row = conn.execute(
            "SELECT slug FROM clients WHERE id = %(id)s",
            {"id": client_id},
        ).fetchone()
        if not slug_row:
            raise RuntimeError(f"No client found for session id {client_id}")
        client_slug = slug_row[0]

        written = 0
        for _, row in df.iterrows():
            conn.execute(
                """
                INSERT INTO customers (
                    client_id, client_uuid, name, address, city, state,
                    lat, lng, category, sqft, monthly_rev, status
                )
                VALUES (
                    %(client_id)s, %(client_uuid)s, %(name)s, %(address)s,
                    %(city)s, %(state)s, %(lat)s, %(lng)s, %(category)s,
                    %(sqft)s, %(monthly_rev)s, %(status)s
                )
                ON CONFLICT (client_id, address) DO UPDATE SET
                    name        = EXCLUDED.name,
                    city        = EXCLUDED.city,
                    state       = EXCLUDED.state,
                    lat         = COALESCE(EXCLUDED.lat, customers.lat),
                    lng         = COALESCE(EXCLUDED.lng, customers.lng),
                    category    = EXCLUDED.category,
                    sqft        = COALESCE(EXCLUDED.sqft, customers.sqft),
                    monthly_rev = EXCLUDED.monthly_rev,
                    status      = EXCLUDED.status,
                    client_uuid = EXCLUDED.client_uuid
                """,
                {
                    "client_id": client_slug,
                    "client_uuid": client_id,
                    "name": str(row["name"]).strip(),
                    "address": str(row["address"]).strip(),
                    "city": str(row.get("city") or "Chicago").strip(),
                    "state": str(row.get("state") or "IL").strip(),
                    "lat": _coerce_numeric(row.get("lat")),
                    "lng": _coerce_numeric(row.get("lng")),
                    "category": str(row["category"]).strip(),
                    "sqft": int(_coerce_numeric(row.get("sqft")) or 0) or None,
                    "monthly_rev": float(row["monthly_revenue_estimate"]),
                    "status": str(row.get("status") or "active").strip(),
                },
            )
            written += 1
    return written


def render(client_id: str) -> None:
    st.title("Upload Customers")
    st.caption(
        "Upload your customer book as CSV. We use it to filter out leads you "
        "already serve and to estimate margin uplift on new prospects."
    )

    with st.expander("CSV format", expanded=False):
        st.markdown(
            "**Required columns**\n\n"
            "- `name` — business name\n"
            "- `address` — street address\n"
            "- `category` — e.g. dental clinic, vet clinic, office, gym\n"
            "- `monthly_revenue_estimate` — monthly contract value in USD (numeric)\n\n"
            "**Optional columns**: `lat`, `lng` (we'll geocode if missing), "
            "`city`, `state`, `sqft`, `status`."
        )

    uploaded = st.file_uploader("Choose a CSV file", type=["csv"])
    if not uploaded:
        return

    raw_bytes = uploaded.read()
    df, errors, bad_rows = parse_csv_bytes(raw_bytes)

    # Parser-level failures come back as a single error with an empty frame.
    if df.empty and errors and errors[0].startswith("Could not parse CSV"):
        st.error(errors[0])
        return

    st.markdown(f"**Rows in file:** {len(df)}")

    if errors:
        for msg in errors:
            st.warning(msg)
        if not bad_rows.empty:
            st.markdown("**Rows that will be skipped:**")
            st.dataframe(bad_rows.head(50), use_container_width=True)
        # If headers are missing entirely, don't show preview / save button.
        if any("Missing required" in e for e in errors):
            return

    st.markdown("**Preview (first 20 rows):**")
    st.dataframe(df.head(20), use_container_width=True)

    valid_count = len(df) - len(bad_rows)
    st.markdown(f"**Valid rows ready to save:** {valid_count}")

    if valid_count == 0:
        st.error("No valid rows to save. Fix the errors above and re-upload.")
        return

    if st.button("Save to my customer book", type="primary"):
        try:
            written = _save_to_db(df, client_id=client_id)
        except Exception as exc:  # noqa: BLE001 - DB errors surface to operator
            st.error(f"Save failed: {exc}")
            return
        st.success(
            f"Saved {written} customer row(s). They're now part of your dedup + "
            f"margin baseline for next Monday's brief."
        )
