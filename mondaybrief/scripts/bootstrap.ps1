# MondayBrief — one-shot bootstrap (Windows PowerShell).
#
# Usage:
#   .\scripts\bootstrap.ps1
#   .\scripts\bootstrap.ps1 -SkipSchema       # don't run psql
#   .\scripts\bootstrap.ps1 -SkipDeps         # don't pip install
#
# What it does:
#   1. python -m venv .venv  (if missing)
#   2. pip install -r requirements.txt
#   3. generate local secrets (MAGIC_LINK_SECRET, FEEDBACK_TOKEN_SECRET, POSTMARK_WEBHOOK_TOKEN)
#      and append them to .env if .env exists; otherwise scaffold .env from .env.example
#   4. psql $env:DATABASE_URL -f schema.sql   (only if DATABASE_URL is set and SkipSchema not passed)
#   5. py_compile every .py under src/mondaybrief  (smoke)
#
# Does NOT:
#   - Sign you up for Stripe / Postmark / Langfuse / Inngest / Neon (manual; see scripts/PROVISIONING.md)
#   - Warm up Postmark domain reputation (DNS work, manual)
#   - Pull the real Spotless customer CSV (blocked on outreach)

[CmdletBinding()]
param(
    [switch]$SkipDeps,
    [switch]$SkipSchema
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "==> MondayBrief bootstrap from $RepoRoot"

# ---- 1. venv -----------------------------------------------------------
if (-not (Test-Path ".venv")) {
    Write-Host "==> Creating .venv"
    python -m venv .venv
}
$Activate = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $Activate) {
    . $Activate
} else {
    Write-Warning "Could not source venv activation script ($Activate). Continuing with system python."
}

# ---- 2. deps -----------------------------------------------------------
if (-not $SkipDeps) {
    Write-Host "==> pip install -r requirements.txt"
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
}

# ---- 3. local secrets --------------------------------------------------
if (-not (Test-Path ".env")) {
    Write-Host "==> Scaffolding .env from .env.example"
    Copy-Item ".env.example" ".env"
}

Write-Host "==> Generating local secrets (if missing in .env)"
python scripts\gen_secrets.py

# ---- 4. schema ---------------------------------------------------------
if (-not $SkipSchema) {
    if ($env:DATABASE_URL) {
        Write-Host "==> psql -f schema.sql"
        psql $env:DATABASE_URL -f schema.sql
    } else {
        Write-Warning "DATABASE_URL not set in env — skipping schema apply. Set it then re-run with -SkipDeps."
    }
} else {
    Write-Host "==> Skipping schema apply (-SkipSchema)"
}

# ---- 5. compile smoke --------------------------------------------------
Write-Host "==> py_compile every src/mondaybrief/*.py"
$pyFiles = Get-ChildItem -Path "src\mondaybrief" -Filter "*.py" -Recurse
foreach ($f in $pyFiles) {
    python -m py_compile $f.FullName
}
Write-Host "==> py_compile OK ($($pyFiles.Count) files)"

Write-Host ""
Write-Host "BOOTSTRAP DONE."
Write-Host "Next steps (manual — see scripts/PROVISIONING.md):"
Write-Host "  1. Sign up for Stripe / Postmark / Langfuse / Inngest / Neon"
Write-Host "  2. Paste keys into .env"
Write-Host "  3. python scripts/seed_clients.py    # seed pilot row"
Write-Host "  4. uvicorn mondaybrief.main:app --reload --port 8000        # public surface"
Write-Host "  5. uvicorn mondaybrief.inngest.server:app --reload --port 8288  # cron + per-client runs"
Write-Host "  6. streamlit run src/mondaybrief/ui/streamlit_app.py        # owner dashboard"
