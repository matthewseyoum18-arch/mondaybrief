"""CLI entry point.

Examples:
  python -m scripts.run_pipeline --client ek --offline
  python -m scripts.run_pipeline --client ek --send-to owner@ekcommercialcleaning.com
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

# Allow running directly with `python scripts/run_pipeline.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mondaybrief.pipeline import CLIENT_PROFILES, run_for_client
from mondaybrief.send.brief import send_brief


def main() -> int:
    parser = argparse.ArgumentParser(description="MondayBrief pipeline runner")
    parser.add_argument("--client", required=True, choices=list(CLIENT_PROFILES))
    parser.add_argument("--offline", action="store_true", help="Use fixtures, no network")
    parser.add_argument("--send-to", default=None, help="Recipient email. Omit to skip send.")
    parser.add_argument("--out-dir", default="out", help="Directory for PDF output")
    args = parser.parse_args()

    bundle, tel = run_for_client(args.client, offline=args.offline, out_dir=args.out_dir)

    print(f"[mondaybrief] {bundle.client_name} — week of {bundle.week_of}")
    print(f"  permits pulled : {tel.permits_pulled}")
    print(f"  geocoded       : {tel.geocoded}")
    print(f"  inside area    : {tel.inside_area}")
    print(f"  after dedup    : {tel.after_dedup}")
    print(f"  scored         : {tel.scored}")
    print(f"  pdf            : {tel.pdf_path}")
    print(f"  cost           : ${tel.cost_usd:.4f}")

    if args.send_to:
        if args.offline:
            print("  send           : skipped (offline)")
        else:
            msg_id = send_brief(bundle, tel.pdf_path, args.send_to)
            print(f"  send           : {msg_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
