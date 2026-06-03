"""Per-run cost rollup.

Each pipeline step returns its incremental USD cost. At the end of
``pipeline.run_for_client`` we sum them and write to
``pipeline_runs.cost_usd`` so the Streamlit dashboard can show the
operator unit economics.

Reference rates (2026-05):
  - Geocodio forward lookup ............ $0.001 / address
  - Mapbox isochrone (free tier) ....... $0.000 (50k/mo free)
  - Twilio Lookup v2 caller name ....... $0.008 / lookup
  - Claude Haiku (LLM) ................. pulled from Anthropic usage
    block on the response (input + output tokens × posted rates) or
    Langfuse trace, whichever is available.

The LLM cost is computed in :mod:`score.claude_score` and surfaced as
part of the step's return value so this module stays pure-arithmetic.
"""
from __future__ import annotations

from typing import Optional

from ..db import connect


def update_run_cost(
    pipeline_run_id: int,
    llm_cost_usd: float,
    geocoding_cost_usd: float = 0.0,
    mapbox_cost_usd: float = 0.0,
    twilio_cost_usd: float = 0.0,
) -> Optional[float]:
    """Sum incremental costs and UPDATE ``pipeline_runs.cost_usd``.

    Returns the total written, or ``None`` if no row was updated (e.g.
    in offline mode when ``pipeline_run_id`` is 0/missing).
    """
    if not pipeline_run_id:
        return None

    total = float(
        (llm_cost_usd or 0.0)
        + (geocoding_cost_usd or 0.0)
        + (mapbox_cost_usd or 0.0)
        + (twilio_cost_usd or 0.0)
    )

    with connect() as conn:
        conn.execute(
            "UPDATE pipeline_runs SET cost_usd = %s WHERE id = %s",
            (round(total, 4), pipeline_run_id),
        )
    return total


# Per-call unit prices (USD). Centralised so the pipeline doesn't have
# pricing math sprinkled across modules.
GEOCODIO_PRICE_PER_LOOKUP = 0.001
MAPBOX_PRICE_PER_ISOCHRONE = 0.0  # free tier
TWILIO_LOOKUP_PRICE = 0.008


def geocodio_cost(lookups: int) -> float:
    return max(0, lookups) * GEOCODIO_PRICE_PER_LOOKUP


def mapbox_cost(isochrones: int) -> float:
    return max(0, isochrones) * MAPBOX_PRICE_PER_ISOCHRONE


def twilio_cost(lookups: int) -> float:
    return max(0, lookups) * TWILIO_LOOKUP_PRICE
