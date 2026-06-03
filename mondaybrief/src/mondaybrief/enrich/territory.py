"""H3 hexagon territory math.

Repo: https://github.com/uber/h3-py
License: Apache-2.0

Given a cleaner's customer set, we compute a hex set that represents their
service area, then drop any new lead whose hex is outside the union of
k-ring neighbors at resolution 9.
"""
from __future__ import annotations
from typing import Iterable
import h3
from ..config import get_settings


def cell_for(lat: float, lng: float, *, resolution: int | None = None) -> str:
    resolution = resolution if resolution is not None else get_settings().h3_resolution
    return h3.latlng_to_cell(lat, lng, resolution)


def service_area_cells(
    customer_cells: Iterable[str],
    *,
    k_ring: int = 4,
) -> set[str]:
    """Expand each customer cell by `k_ring` neighbors to model 'within driving range'.

    At resolution 9, k=4 ≈ 600m radius — roughly a 15-min off-peak drive on Chicago grid.
    """
    expanded: set[str] = set()
    for cell in customer_cells:
        if not cell:
            continue
        expanded.update(h3.grid_disk(cell, k_ring))
    return expanded


def inside_service_area(cell: str, service_cells: set[str]) -> bool:
    return cell in service_cells


def km_between(cell_a: str, cell_b: str) -> float:
    """Great-circle km between two H3 cell centers — useful for nearest-customer fallback."""
    return h3.great_circle_distance(
        h3.cell_to_latlng(cell_a),
        h3.cell_to_latlng(cell_b),
        unit="km",
    )
