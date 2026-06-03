"""Seed pilot ``clients`` rows.

Usage:

  python scripts/seed_clients.py                # seed both Spotless + Pritchard
  python scripts/seed_clients.py --slug spotless
  python scripts/seed_clients.py --slug pritchard
  python scripts/seed_clients.py --dry-run      # print SQL, don't execute

Reads DATABASE_URL from the env (via the standard mondaybrief.config settings).
INSERT ... ON CONFLICT (slug) DO UPDATE so re-running is idempotent.

The contact_email defaults are placeholders. Override with --email when you
have the real cleaner's address (Pritchard contact pending; Spotless contact
pending until they share their customer book).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# allow direct `python scripts/seed_clients.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mondaybrief.db import connect  # noqa: E402


PILOTS = {
    "spotless": {
        "name": "Spotless Cleaning Chicago",
        "contact_email": "owner@spotlesschicago.com",   # PLACEHOLDER — replace before live send
        "metros": ["chicago"],
    },
    "pritchard": {
        "name": "Pritchard Industries",
        "contact_email": "contact@pritchardindustries.com",  # PLACEHOLDER
        "metros": ["nyc"],
    },
    "ek": {
        "name": "E&K Commercial Cleaning",
        "contact_email": "contact@ekcommercialcleaning.com",
        "metros": ["chicago"],
    },
}


UPSERT_SQL = """
INSERT INTO clients (slug, name, contact_email, metros, active)
VALUES (%(slug)s, %(name)s, %(email)s, %(metros)s, true)
ON CONFLICT (slug) DO UPDATE
   SET name          = EXCLUDED.name,
       contact_email = EXCLUDED.contact_email,
       metros        = EXCLUDED.metros,
       active        = true
RETURNING id, slug, contact_email
"""


def seed_one(slug: str, *, email_override: str | None = None, dry_run: bool = False) -> None:
    profile = PILOTS[slug]
    email = email_override or profile["contact_email"]
    params = {
        "slug": slug,
        "name": profile["name"],
        "email": email,
        "metros": profile["metros"],
    }

    if dry_run:
        print(f"[seed] DRY-RUN — would upsert clients row:")
        print(f"  slug   = {slug}")
        print(f"  name   = {profile['name']}")
        print(f"  email  = {email}")
        print(f"  metros = {profile['metros']}")
        return

    with connect() as conn:
        row = conn.execute(UPSERT_SQL, params).fetchone()
    print(f"[seed] upserted client_id={row[0]} slug={row[1]} email={row[2]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed MondayBrief pilot clients.")
    parser.add_argument(
        "--slug",
        choices=sorted(PILOTS),
        help="Single pilot to seed. Omit to seed all PILOTS.",
    )
    parser.add_argument("--email", default=None, help="Override contact_email.")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL params, don't execute.")
    args = parser.parse_args()

    targets = [args.slug] if args.slug else list(PILOTS)
    for slug in targets:
        seed_one(slug, email_override=args.email, dry_run=args.dry_run)

    if any(PILOTS[s]["contact_email"].startswith(("owner@", "contact@pritchard")) for s in targets):
        print(
            "\nNOTE: Default contact_email values are placeholders. "
            "Before the first real send, re-run with --email <real-address>.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
