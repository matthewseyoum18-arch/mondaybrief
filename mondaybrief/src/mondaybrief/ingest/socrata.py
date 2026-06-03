"""Chicago Open Data ingest via `sodapy` (MIT).

Pulls three datasets:
  - r5kz-chrr  Business Licenses
  - ydr8-5enu  Building Permits
  - 4ijn-s7e5  Liquor / Public Places of Amusement

Each dataset entry in DATASETS may include an optional `where_filter`
SoQL fragment. When present, it is AND-ed into the date-range WHERE
clause server-side so we never pay Geocodio + Claude for noise rows
(renewals, transfers, revoked permits, etc.).

Current filters:
  - r5kz-chrr  license_status='AAI' AND application_type='ISSUE'
                (drops renewals, change-of-location, revoked)
  - ydr8-5enu  permit_type IN (NEW CONSTRUCTION, RENOVATION/ALTERATION)
                AND status_current IN ('PERMIT ISSUED','OPEN')
                (drops minor/easy permits + cancelled/withdrawn)
  - 4ijn-s7e5  unfiltered — liquor app volume is low enough that we
                want every row.

Repo: https://github.com/afeld/sodapy
License: MIT
"""
from __future__ import annotations
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable
from sodapy import Socrata
from ..config import get_settings
from ..models import RawLead

DOMAIN = "data.cityofchicago.org"

DATASETS = {
    "r5kz-chrr": {
        "name_field": "doing_business_as_name",
        "fallback_name_field": "legal_name",
        "address_field": "address",
        "date_field": "date_issued",
        # license_status: AAI=Active Issued, AAC=Active Cancelled,
        #                 REV=Revoked, REA=Re-issued/Renewed
        # application_type: ISSUE, RENEW, C_LOC (change of location), etc.
        "where_filter": "license_status = 'AAI' AND application_type = 'ISSUE'",
    },
    "ydr8-5enu": {
        "name_field": "contact_1_name",
        "fallback_name_field": "work_description",
        "address_field": "street_name",
        "date_field": "issue_date",
        # Real values from data.cityofchicago.org/dataset/Building-Permits/ydr8-5enu
        "where_filter": (
            "permit_type IN ('PERMIT - NEW CONSTRUCTION', 'PERMIT - RENOVATION/ALTERATION') "
            "AND status_current IN ('PERMIT ISSUED', 'OPEN')"
        ),
    },
    "4ijn-s7e5": {
        "name_field": "doing_business_as_name",
        "fallback_name_field": "legal_name",
        "address_field": "address",
        "date_field": "license_start_date",
        # Intentionally unfiltered — liquor app volume is rare enough that
        # noise isn't the problem; we want every row.
    },
}


def _client() -> Socrata:
    token = get_settings().socrata_app_token or None
    return Socrata(DOMAIN, token, timeout=30)


def pull_since(since: date | None = None) -> list[RawLead]:
    """Pull every new row across the three Chicago datasets issued since `since`.

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
                addr = row.get(schema["address_field"]) or ""
                if not name or not addr:
                    continue
                date_issued_str = row.get(schema["date_field"])
                leads.append(RawLead(
                    source=source_id,
                    source_id=str(row.get(":id") or row.get("id") or f"{source_id}-{addr}"),
                    name=name.strip(),
                    dba=row.get("doing_business_as_name"),
                    address=addr.strip(),
                    city=row.get("city", "Chicago"),
                    state=row.get("state", "IL"),
                    zip=row.get("zip_code") or row.get("zip"),
                    date_issued=date.fromisoformat(date_issued_str[:10]) if date_issued_str else None,
                    raw_json=dict(row),
                ))
    return leads


def load_fixture(fixture_path: str | Path) -> list[RawLead]:
    """Offline mode — load mocked Socrata rows from a fixture JSON file."""
    raw = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    return [RawLead(**row) for row in raw]


def chunked(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
