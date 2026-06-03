"""NYC Open Data ingest via `sodapy` (MIT).

Pulls two datasets:
  - ipu4-2q9a  DOB Now: Build — Approved Permits
  - ic3t-wcy2  Legally Operating Businesses (DCWP licenses)

Each dataset entry in DATASETS may include an optional `where_filter`
SoQL fragment. When present, it is AND-ed into the date-range WHERE
clause server-side so we never pay Geocodio + Claude for noise rows
(non-TI permits, expired licenses, etc.).

Current filters:
  - ipu4-2q9a  job_type IN ('NB','A1','A2','A3') AND filing_status='P'
                (NB=New Building; A1/A2/A3=Alterations Type 1/2/3;
                 filing_status P=Permit Issued — drops applications,
                 withdrawals, and minor work)
  - ic3t-wcy2  license_status='Active'
                (drops expired/inactive DCWP licenses)

Repo: https://github.com/afeld/sodapy
License: MIT
"""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
from sodapy import Socrata
from ..config import get_settings
from ..models import RawLead
from . import socrata as chicago_socrata

DOMAIN = "data.cityofnewyork.us"

DATASETS = {
    "ipu4-2q9a": {
        "name_field": "owner_s_business_name",
        "fallback_name_field": "owner_s_first_name",
        "address_fields": ("house_no", "street_name"),
        "date_field": "issued_date",
        # job_type: NB=New Building, A1/A2/A3=Alterations Type 1/2/3
        # filing_status: P=Permit Issued
        "where_filter": "job_type IN ('NB', 'A1', 'A2', 'A3') AND filing_status = 'P'",
    },
    "ic3t-wcy2": {
        "name_field": "business_name",
        "fallback_name_field": "business_name_2",
        "address_fields": ("address_building", "address_street_name"),
        "date_field": "license_creation_date",
        "where_filter": "license_status = 'Active'",
    },
    # TODO: NY State Liquor Authority (SLA) licenses are NOT hosted on
    # data.cityofnewyork.us — they live at the state level (data.ny.gov or
    # the SLA's own portal). Add a dedicated `ingest/ny_sla.py` module to
    # cover on-premises liquor licensees in NYC. Until then, NYC bar/
    # restaurant signals come only from DCWP + DOB permits here.
}


def _client() -> Socrata:
    token = get_settings().socrata_app_token or None
    return Socrata(DOMAIN, token, timeout=30)


def _join_address(row: dict, fields: tuple[str, ...]) -> str:
    parts = [str(row.get(f, "") or "").strip() for f in fields]
    return " ".join(p for p in parts if p).strip()


def pull_since(since: date | None = None) -> list[RawLead]:
    """Pull every new row across the two NYC datasets issued since `since`.

    Defaults to the previous 7 days. Uses the free app-token rate budget.
    """
    since = since or (date.today() - timedelta(days=7))
    leads: list[RawLead] = []
    with _client() as client:
        for source_id, schema in DATASETS.items():
            where = f"{schema['date_field']} >= '{since.isoformat()}'"
            if schema.get("where_filter"):
                where = f"({where}) AND ({schema['where_filter']})"
            rows = client.get(source_id, where=where, limit=5000)
            for row in rows:
                name = row.get(schema["name_field"]) or row.get(schema["fallback_name_field"]) or ""
                addr = _join_address(row, schema["address_fields"])
                if not name or not addr:
                    continue
                date_issued_str = row.get(schema["date_field"])
                leads.append(RawLead(
                    source=f"nyc:{source_id}",
                    source_id=str(row.get(":id") or row.get("id") or f"{source_id}-{addr}"),
                    name=name.strip(),
                    dba=row.get("business_name") or row.get("owner_s_business_name"),
                    address=addr,
                    city=row.get("city", "New York"),
                    state=row.get("state", "NY"),
                    zip=row.get("zip_code") or row.get("zip") or row.get("address_zip"),
                    date_issued=date.fromisoformat(date_issued_str[:10]) if date_issued_str else None,
                    raw_json=dict(row),
                ))
    return leads


def load_fixture(fixture_path: str | Path) -> list[RawLead]:
    """Offline mode — delegate to the Chicago module's fixture loader.

    The fixture JSON shape is identical (a list of RawLead dicts), so the
    Chicago loader is reused verbatim.
    """
    return chicago_socrata.load_fixture(fixture_path)


# Dataset references (verify here):
#   ipu4-2q9a  https://data.cityofnewyork.us/dataset/DOB-NOW-Build-Approved-Permits/ipu4-2q9a
#   ic3t-wcy2  https://data.cityofnewyork.us/dataset/Legally-Operating-Businesses/ic3t-wcy2
