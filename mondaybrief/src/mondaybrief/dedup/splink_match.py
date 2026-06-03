"""Probabilistic record linkage with Splink.

Repo: https://github.com/moj-analytical-services/splink
License: MIT (UK Ministry of Justice)

We treat the cleaner's customer book as one record set and the week's enriched
leads as another, then ask Splink to score every cross-pair. Any pair scoring
above ``match_threshold`` is dropped from the lead pool — we never pitch a
business the cleaner already serves.

A second pass also drops anything tagged ``status='lost'`` on a customer row
(historical losing bids).
"""
from __future__ import annotations
import pandas as pd
from splink import DuckDBAPI, Linker, SettingsCreator, block_on
from splink.comparison_library import ExactMatch, JaroWinklerAtThresholds, LevenshteinAtThresholds

from ..models import Customer, EnrichedLead

# Cutoff calibrated on initial pilot data — raise to 0.95 for tighter dedup,
# lower to 0.80 if cleaners report missing matches.
DEFAULT_MATCH_THRESHOLD = 0.90


def _normalize_df(rows: list[dict], unique_prefix: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["unique_id"] = [f"{unique_prefix}-{i}" for i in range(len(df))]
    for col in ("name", "address"):
        if col in df.columns:
            df[col] = df[col].fillna("").str.lower().str.strip()
    return df


def _settings() -> SettingsCreator:
    return SettingsCreator(
        link_type="link_only",
        blocking_rules_to_generate_predictions=[
            block_on("address"),
            block_on("name"),
        ],
        comparisons=[
            JaroWinklerAtThresholds("name", [0.92, 0.85]),
            LevenshteinAtThresholds("address", [1, 3]),
            ExactMatch("city"),
        ],
        retain_intermediate_calculation_columns=False,
    )


def _splink_match_ids(
    customers: list[Customer],
    leads: list[EnrichedLead],
    match_threshold: float,
) -> tuple[set[str], set[str]]:
    """Probabilistic match. Returns (matched_lead_ids, lost_lead_ids) as
    ``lead-{i}`` strings. Raises if Splink/DuckDB fails — caller falls back."""
    customer_df = _normalize_df([c.model_dump() for c in customers], "cust")
    lead_df = _normalize_df([l.model_dump() for l in leads], "lead")

    linker = Linker(
        [customer_df, lead_df],
        _settings(),
        db_api=DuckDBAPI(),
        input_table_aliases=["customers", "leads"],
    )
    linker.training.estimate_probability_two_random_records_match(
        [block_on("address"), block_on("name")],
        recall=0.8,
    )
    linker.training.estimate_u_using_random_sampling(max_pairs=1_000_000)

    predictions = linker.inference.predict(threshold_match_probability=match_threshold)
    pred_df = predictions.as_pandas_dataframe()

    # unique_id_l = customer, unique_id_r = lead (link_only honors input order)
    matched = set(pred_df.get("unique_id_r", pd.Series(dtype=str)).tolist())
    lost_customer_ids = {f"cust-{i}" for i, c in enumerate(customers) if c.status in ("lost", "bid")}
    lost = (
        set(pred_df.loc[pred_df["unique_id_l"].isin(lost_customer_ids), "unique_id_r"].tolist())
        if "unique_id_l" in pred_df.columns
        else set()
    )
    return matched, lost


def _exact_match_ids(
    customers: list[Customer], leads: list[EnrichedLead]
) -> tuple[set[str], set[str]]:
    """Deterministic fallback: exact normalized (name|address) match. Coarser
    than Splink but never crashes — keeps a client run alive when DuckDB/Splink
    misbehaves."""
    def key(name: str | None, addr: str | None) -> str:
        return f"{(name or '').lower().strip()}|{(addr or '').lower().strip()}"

    cust_keys = {key(c.name, c.address) for c in customers}
    lost_keys = {key(c.name, c.address) for c in customers if c.status in ("lost", "bid")}
    matched: set[str] = set()
    lost: set[str] = set()
    for i, lead in enumerate(leads):
        k = key(lead.name, lead.address)
        if k in lost_keys:
            lost.add(f"lead-{i}")
        elif k in cust_keys:
            matched.add(f"lead-{i}")
    return matched, lost


def drop_existing_customers(
    customers: list[Customer],
    leads: list[EnrichedLead],
    *,
    match_threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> tuple[list[EnrichedLead], dict]:
    """Return (surviving_leads, telemetry_dict).

    Tries probabilistic Splink dedup; on any Splink/DuckDB failure, falls back
    to deterministic exact name+address matching so one library error can't kill
    the whole client run. Telemetry carries a ``method`` flag for visibility.
    """
    if not leads:
        return [], {"in": 0, "dropped_existing": 0, "dropped_lost_bid": 0, "out": 0, "method": "none"}
    if not customers:
        return list(leads), {"in": len(leads), "dropped_existing": 0, "dropped_lost_bid": 0, "out": len(leads), "method": "none"}

    try:
        matched_lead_ids, lost_lead_ids = _splink_match_ids(customers, leads, match_threshold)
        method = "splink"
    except Exception:
        matched_lead_ids, lost_lead_ids = _exact_match_ids(customers, leads)
        method = "exact_fallback"

    survivors: list[EnrichedLead] = []
    dropped_existing = 0
    dropped_lost_bid = 0
    for i, lead in enumerate(leads):
        lid = f"lead-{i}"
        if lid in lost_lead_ids:
            dropped_lost_bid += 1
        elif lid in matched_lead_ids:
            dropped_existing += 1
        else:
            survivors.append(lead)

    return survivors, {
        "in": len(leads),
        "dropped_existing": dropped_existing,
        "dropped_lost_bid": dropped_lost_bid,
        "out": len(survivors),
        "method": method,
    }
