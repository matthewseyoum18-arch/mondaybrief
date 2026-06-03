"""Geocoding via Geocodio.

Geocodio is a paid service with a 2,500/day free tier. We use the official
Python client; for offline tests we fall back to a deterministic stub that
returns approximate Chicago coordinates derived from the address string hash.
Results below GEOCODE_MIN_ACCURACY are dropped to prevent phantom service-area
matches from polluting downstream H3 cell assignment.
"""
from __future__ import annotations
import hashlib
from typing import Optional
from geocodio import GeocodioClient
from ..config import get_settings


CHI_LAT_RANGE = (41.6445, 42.0231)
CHI_LNG_RANGE = (-87.9401, -87.5240)
NYC_LAT_RANGE = (40.4774, 40.9176)
NYC_LNG_RANGE = (-74.2591, -73.7004)

# Tokens that mark an address as NYC-metro so the offline stub places it in the
# NYC bbox instead of defaulting every lead into Chicago.
_NYC_TOKENS = (
    ", ny", " new york", "brooklyn", "queens", "bronx", "manhattan",
    "staten island", "long island city", " nyc",
)

# Geocodio accuracy_score threshold. Per Geocodio docs: 'rooftop' ~0.95+,
# 'range_interpolation' 0.7-0.9, 'state' often <0.5. We require 0.85 to
# ensure we're matching to a real address (not a centroid of a city/zip/state).
GEOCODE_MIN_ACCURACY = 0.85


def _client() -> GeocodioClient:
    return GeocodioClient(get_settings().geocodio_api_key)


def forward(address: str, *, offline: bool = False) -> tuple[Optional[float], Optional[float]]:
    """Return (lat, lng) for a single address. Returns (None, None) on miss."""
    if offline or not get_settings().geocodio_api_key:
        return _stub_geocode(address)
    try:
        result = _client().geocode(address)
        if not result or not result.get("results"):
            return (None, None)
        top = result["results"][0]
        if float(top.get("accuracy_score", 0.0)) < GEOCODE_MIN_ACCURACY:
            return (None, None)
        loc = top["location"]
        return (float(loc["lat"]), float(loc["lng"]))
    except Exception:
        return (None, None)


def batch_forward(addresses: list[str], *, offline: bool = False) -> list[tuple[Optional[float], Optional[float]]]:
    """Batch-geocode. Stays under Geocodio's batch limits (max 10k per POST)."""
    if offline or not get_settings().geocodio_api_key:
        return [_stub_geocode(a) for a in addresses]
    try:
        result = _client().geocode(addresses)
        out: list[tuple[Optional[float], Optional[float]]] = []
        for r in result.get("results", []):
            inner = r.get("response", {}).get("results")
            if inner:
                top = inner[0]
                if float(top.get("accuracy_score", 0.0)) < GEOCODE_MIN_ACCURACY:
                    out.append((None, None))
                    continue
                loc = top["location"]
                out.append((float(loc["lat"]), float(loc["lng"])))
            else:
                out.append((None, None))
        return out
    except Exception:
        return [(None, None) for _ in addresses]


def _stub_geocode(address: str) -> tuple[float, float]:
    """Deterministic offline geocoder — hashes address into the right metro bbox.

    Chicago by default; NYC when the address carries an NYC-metro token. This
    keeps offline NYC leads inside their own service area instead of collapsing
    them into Chicago. Good enough for offline smoke tests; never used in prod.
    """
    lower = address.lower()
    if any(tok in lower for tok in _NYC_TOKENS):
        lat_range, lng_range = NYC_LAT_RANGE, NYC_LNG_RANGE
    else:
        lat_range, lng_range = CHI_LAT_RANGE, CHI_LNG_RANGE
    digest = hashlib.md5(lower.encode("utf-8")).digest()
    lat_frac = digest[0] / 255.0
    lng_frac = digest[1] / 255.0
    lat = lat_range[0] + lat_frac * (lat_range[1] - lat_range[0])
    lng = lng_range[0] + lng_frac * (lng_range[1] - lng_range[0])
    return (round(lat, 6), round(lng, 6))
