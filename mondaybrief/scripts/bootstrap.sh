#!/usr/bin/env bash
# MondayBrief — one-shot bootstrap (POSIX / WSL / mac / Linux).
#
# Usage:
#   bash scripts/bootstrap.sh
#   SKIP_DEPS=1 bash scripts/bootstrap.sh
#   SKIP_SCHEMA=1 bash scripts/bootstrap.sh
#
# What it does:
#   1. python -m venv .venv  (if missing)
#   2. pip install -r requirements.txt
#   3. generate local secrets (MAGIC_LINK_SECRET, FEEDBACK_TOKEN_SECRET, POSTMARK_WEBHOOK_TOKEN)
#   4. psql "$DATABASE_URL" -f schema.sql   (only if DATABASE_URL is set)
#   5. py_compile every .py under src/mondaybrief
#
# Does NOT auto-provision external services. See scripts/PROVISIONING.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> MondayBrief bootstrap from $REPO_ROOT"

# ---- 1. venv -----------------------------------------------------------
if [ ! -d ".venv" ]; then
  echo "==> Creating .venv"
  python -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate 2>/dev/null || . .venv/Scripts/activate 2>/dev/null || true

# ---- 2. deps -----------------------------------------------------------
if [ -z "${SKIP_DEPS:-}" ]; then
  echo "==> pip install -r requirements.txt"
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
fi

# ---- 3. local secrets --------------------------------------------------
if [ ! -f ".env" ]; then
  echo "==> Scaffolding .env from .env.example"
  cp .env.example .env
fi

echo "==> Generating local secrets (if missing in .env)"
python scripts/gen_secrets.py

# ---- 4. schema ---------------------------------------------------------
if [ -z "${SKIP_SCHEMA:-}" ]; then
  if [ -n "${DATABASE_URL:-}" ]; then
    echo "==> psql -f schema.sql"
    psql "$DATABASE_URL" -f schema.sql
  else
    echo "WARN: DATABASE_URL not set — skipping schema apply."
  fi
else
  echo "==> Skipping schema apply (SKIP_SCHEMA=1)"
fi

# ---- 5. compile smoke --------------------------------------------------
echo "==> py_compile every src/mondaybrief/*.py"
find src/mondaybrief -name '*.py' -print0 | xargs -0 -n1 python -m py_compile
echo "==> py_compile OK"

cat <<EOF

BOOTSTRAP DONE.
Next steps (manual — see scripts/PROVISIONING.md):
  1. Sign up for Stripe / Postmark / Langfuse / Inngest / Neon
  2. Paste keys into .env
  3. python scripts/seed_clients.py
  4. uvicorn mondaybrief.main:app --reload --port 8000
  5. uvicorn mondaybrief.inngest.server:app --reload --port 8288
  6. streamlit run src/mondaybrief/ui/streamlit_app.py
EOF
