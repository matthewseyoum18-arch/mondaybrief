"""NYC Socrata ingest tests — fully offline (no network).

Mirrors the style of test_pipeline_smoke.py:
  - sodapy is never called; rows come from fixtures/nyc_sample_permits.json
  - the Pritchard seed customer book is exercised at the CSV level
  - DATASETS / DOMAIN constants are pinned so a rename trips the suite
"""
from __future__ import annotations
import csv
import json
from pathlib import Path

import pytest

from mondaybrief.ingest import nyc_socrata


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
NYC_FIXTURE = FIXTURES / "nyc_sample_permits.json"
PRITCHARD_FIXTURE = FIXTURES / "pritchard_customers.csv"


def test_nyc_load_fixture_parses_rows() -> None:
    leads = nyc_socrata.load_fixture(NYC_FIXTURE)

    assert len(leads) >= 15, f"expected >= 15 NYC fixture rows, got {len(leads)}"

    for lead in leads:
        assert lead.source.startswith("nyc:"), f"bad source prefix: {lead.source!r}"
        assert lead.city == "New York", f"bad city: {lead.city!r}"
        assert lead.state == "NY", f"bad state: {lead.state!r}"


def test_pritchard_fixture_schema() -> None:
    assert PRITCHARD_FIXTURE.exists(), f"missing fixture: {PRITCHARD_FIXTURE}"

    with PRITCHARD_FIXTURE.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        rows = list(reader)

    required = {"name", "address", "lat", "lng", "category", "monthly_rev"}
    missing = required - set(header)
    assert not missing, f"pritchard_customers.csv missing columns: {missing}"

    assert len(rows) >= 20, f"expected >= 20 Pritchard seed rows, got {len(rows)}"

    for i, row in enumerate(rows):
        assert row["client_id"] == "pritchard", (
            f"row {i} client_id={row.get('client_id')!r}, expected 'pritchard'"
        )
        lat = float(row["lat"])
        lng = float(row["lng"])
        assert 40.49 < lat < 40.92, f"row {i} lat={lat} outside NYC bbox"
        assert -74.27 < lng < -73.69, f"row {i} lng={lng} outside NYC bbox"


def test_nyc_datasets_have_real_ids() -> None:
    # Real NYC Socrata IDs — guards against accidental renames.
    assert "ipu4-2q9a" in nyc_socrata.DATASETS
    assert "ic3t-wcy2" in nyc_socrata.DATASETS


def test_nyc_socrata_domain() -> None:
    assert nyc_socrata.DOMAIN == "data.cityofnewyork.us"
