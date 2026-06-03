"""Drive-time enrichment — populate ``EnrichedLead.drive_minutes``.

The scoring engine's route component needs how far a lead sits off the
cleaner's existing route. ``EnrichedLead.drive_minutes`` was declared in the
model but never filled in, so "route fit" was effectively guesswork. This
module fills it.

v1 uses a deterministic H3 great-circle proxy: distance from the lead's hex to
its nearest customer hex, times a city-driving minutes-per-km factor. This is
free, offline-safe, and reproducible (important for tests). A Mapbox Matrix
upgrade — real road drive-times — is a drop-in replacement at
:func:`_minutes_between` when a key is configured; the rest of the pipeline is
unchanged.
"""
from __future__ import annotations

from ..models import Customer, EnrichedLead
from ..score import economics
from .territory import km_between

KM_PER_MILE = 1.609344


def _minutes_between(cell_a: str, cell_b: str) -> float:
    """Proxy drive-minutes between two H3 cells: great-circle km × min/km.

    Extension point: when a Mapbox/OSRM matrix is available, replace this body
    with a real road-network lookup keyed on the cell centroids. Callers and
    the scoring engine are agnostic to which one runs.
    """
    km = km_between(cell_a, cell_b)
    return round(km * economics.DRIVE_MINUTES_PER_KM, 2)


def nearest_customer_by_hex(
    lead: EnrichedLead, customers: list[Customer]
) -> tuple[Customer | None, float | None]:
    """Return (nearest_customer, drive_minutes) using H3 distance.

    Returns (None, None) when the lead or every customer lacks an H3 cell.
    """
    if not lead.h3_cell:
        return None, None
    best: Customer | None = None
    best_min: float | None = None
    for c in customers:
        if not c.h3_cell:
            continue
        minutes = _minutes_between(lead.h3_cell, c.h3_cell)
        if best_min is None or minutes < best_min:
            best, best_min = c, minutes
    return best, best_min


def annotate_drive_times(
    leads: list[EnrichedLead], customers: list[Customer]
) -> list[EnrichedLead]:
    """Mutate each lead in place with drive_minutes + nearest-customer fields.

    Idempotent and side-effect-free beyond the in-place field writes; returns
    the same list for convenient chaining.
    """
    for lead in leads:
        nearest, minutes = nearest_customer_by_hex(lead, customers)
        if nearest is not None and minutes is not None:
            lead.drive_minutes = minutes
            lead.nearest_customer_id = nearest.id
            if lead.h3_cell and nearest.h3_cell:
                lead.nearest_customer_distance_mi = round(
                    km_between(lead.h3_cell, nearest.h3_cell) / KM_PER_MILE, 2
                )
    return leads
