"""Generate local random secrets and write them into .env (idempotent).

Two secrets must be set in .env before the app boots safely:

* ``MAGIC_LINK_SECRET``       — itsdangerous serializer for Streamlit sign-in tokens
* ``FEEDBACK_TOKEN_SECRET``   — itsdangerous serializer for per-lead feedback + unsubscribe links

(The Resend webhook secret ``RESEND_WEBHOOK_SECRET`` is NOT generated here — it
comes from the Resend dashboard's webhook config; see PROVISIONING.md.)

This script reads .env, generates a 32-byte URL-safe random value for any of the
two that is missing (or set to an empty string), and writes the file back in
place. Existing non-empty values are NEVER overwritten — re-running is safe.

Run after bootstrap.ps1 / bootstrap.sh; both call this script automatically.
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".env"

KEYS_TO_FILL = [
    "MAGIC_LINK_SECRET",
    "FEEDBACK_TOKEN_SECRET",
]


def _read_env(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _ensure_filled(lines: list[str]) -> tuple[list[str], list[str]]:
    """Return (updated_lines, list_of_keys_we_just_filled)."""
    by_key: dict[str, int] = {}
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        by_key[key] = idx

    filled: list[str] = []

    for key in KEYS_TO_FILL:
        idx = by_key.get(key)
        if idx is not None:
            current_value = lines[idx].split("=", 1)[1].strip()
            if current_value:
                continue
            lines[idx] = f"{key}={secrets.token_urlsafe(32)}"
            filled.append(key)
        else:
            lines.append(f"{key}={secrets.token_urlsafe(32)}")
            filled.append(key)

    return lines, filled


def main() -> int:
    if not ENV_FILE.exists():
        example = REPO_ROOT / ".env.example"
        if example.exists():
            ENV_FILE.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"[gen_secrets] scaffolded {ENV_FILE} from .env.example", file=sys.stderr)
        else:
            ENV_FILE.write_text("", encoding="utf-8")
            print(f"[gen_secrets] created empty {ENV_FILE}", file=sys.stderr)

    lines = _read_env(ENV_FILE)
    updated, filled = _ensure_filled(lines)

    if not filled:
        print("[gen_secrets] all local secrets already set — no changes.")
        return 0

    ENV_FILE.write_text("\n".join(updated) + "\n", encoding="utf-8")
    print(f"[gen_secrets] filled: {', '.join(filled)} in {ENV_FILE}")
    print("[gen_secrets] keep this file out of version control.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
