# MondayBrief v1 — Wire to Real Business E2E

## Context

MondayBrief is a Monday 7am lead-brief PDF for Chicago commercial cleaners — ingests Socrata business licenses + building permits + liquor apps, dedupes against the owner's customer book, scores by proximity/margin, emails a 10–25-prospect PDF with cold-call openers. v1 = cleaners only (POS/security/signage/MSP deferred to v2).

Code is ~70% built: ingest, geocode (Geocodio), H3 indexing, Splink dedup, pgvector RAG, Claude Haiku scoring with prompt caching, WeasyPrint PDF, Postmark delivery, Neon Postgres schema, offline smoke test. Missing: scheduler wiring, billing, dashboard, dunning, monitoring, outreach copy, real customer.

Goal: stand up the **full promised loop** (onboard → schedule → ingest → score → brief → deliver → bill → retain feedback) so one chosen cleaner gets a real Monday brief and pays for it. No fixture-only demos.

## Target Pilot

**Primary**: Spotless Cleaning Chicago — BSCAI member, BBB A+, 50+ years, multi-site. Highest research confidence in vault.
**Backup**: E&K Commercial Cleaning (773-432-8222, contact@ekcommercialcleaning.com) — already on `outreach/Call Pipeline.md` row 12, fixture book already shaped to them.

Pipeline must work for ANY cleaner who uploads their customer CSV. Target = outreach focus, not hardcoding.

## What Gets Wired

### 1. Scheduler — Inngest cron
- Register `pipeline.run` as Inngest function, cron `0 7 * * MON America/Chicago`
- Per-client config: which metros, customer book ref, recipient email
- Failure → Postmark alert to operator
- Files: `mondaybrief/src/mondaybrief/inngest/client.py` (new), `pipeline.py` (add `--client-id` flag)

### 2. Customer Onboarding — CSV upload + ingest
- Streamlit page: upload customer book CSV → validate schema → write to `customers` table tagged by `client_id`
- Required cols: name, address, lat (optional, geocode if missing), category, monthly_revenue_estimate
- Files: `mondaybrief/src/mondaybrief/ui/streamlit_app.py` (new), `mondaybrief/src/mondaybrief/onboard/csv_loader.py` (extract from existing loader logic)

### 3. Billing — Stripe Checkout + recurring + dunning
- Stripe Checkout subscription ($149/mo), client created on first onboarding step before CSV upload
- Webhook handler: `customer.subscription.deleted` → suspend brief delivery; `invoice.payment_failed` → trigger 3-retry dunning email via Postmark
- New table: `subscriptions` (client_id, stripe_sub_id, status, current_period_end)
- Files: `mondaybrief/src/mondaybrief/billing/stripe_client.py` (new), `mondaybrief/src/mondaybrief/billing/webhooks.py` (new), schema migration

### 4. Dashboard — Streamlit (2 pages)
- Page 1: Upload Customers (CSV uploader, schema preview, "save" button)
- Page 2: Past Briefs (list `pipeline_runs` rows, link to S3 / local PDF, cost, lead count, sent_at)
- Auth: Streamlit `st.login` with magic link via Postmark, scoped by `client_id`
- Files: `mondaybrief/src/mondaybrief/ui/streamlit_app.py`, `ui/pages/`, `mondaybrief/src/mondaybrief/auth/magic_link.py`

### 5. Monitoring — Langfuse + cost auto-population
- Wrap Claude calls in Langfuse `@observe` (free tier, 50k events/mo)
- After each pipeline run, write actual `cost_usd` to `pipeline_runs` from Langfuse + Geocodio + Mapbox + Twilio totals
- Postmark webhook → log `delivered` / `bounced` / `opened` per brief
- Files: `mondaybrief/src/mondaybrief/observability/langfuse_setup.py` (new), modify `score/claude_score.py`, `pipeline.py`

### 6. Outreach Sequence — copy + send
- Cold email template (3-touch) targeting Spotless + 5 backup cleaners; subject + body use SCORED real-Chicago-Socrata lead as proof
- Cold-call opener (already drafted in `outreach/Meeting Demo Script.md` — refine)
- Send via Postmark broadcast stream (separate from transactional cleaner stream)
- Files: `outreach/cold_email_v1.md` (new), `outreach/send_outreach.py` (new, one-shot script)

### 7. Feedback Loop — minimal
- Each brief PDF gets unique URL with "thumbs up / down per lead" form (FastAPI endpoint, writes to `lead_feedback` table)
- Future scoring iteration consumes this; v1 just collects
- Files: `mondaybrief/src/mondaybrief/feedback/api.py` (new), schema migration

## Critical Files to Modify

- `mondaybrief/src/mondaybrief/pipeline.py` — add `--client-id`, read per-client config from `clients` table instead of `CLIENT_PROFILES` dict
- `mondaybrief/schema.sql` — add tables: `clients`, `subscriptions`, `lead_feedback`; add `client_id` FK to `customers`, `pipeline_runs`, `scored_leads`
- `mondaybrief/src/mondaybrief/score/claude_score.py` — wrap in Langfuse observe
- `mondaybrief/src/mondaybrief/send/postmark.py` — add broadcast stream support for outreach
- `mondaybrief/scripts/run_pipeline.py` — keep CLI for ops, but Inngest is canonical trigger

## Stack Additions

- **Inngest** — free tier (50k exec/mo). Python SDK.
- **Stripe** — Checkout + Billing. Test mode first, live before pilot signs.
- **Streamlit** — Python-native UI, deploys to Fly.io / Streamlit Cloud. Avoids Next.js scope.
- **Langfuse** — free tier, self-host backup possible (not OSS-banned per memory).

No Lucia, no n8n self-host, no wkhtmltopdf — checked against `project_oss_no_go_list.md`.

## Verification (end-to-end)

1. **Local smoke** (offline mode): `python scripts/run_pipeline.py --client-id=spotless --offline` → PDF in `output/` with real Chicago Socrata fixture data
2. **Stripe sandbox**: Test card 4242 → subscription created → webhook fires → client row marked active
3. **Onboarding flow**: Open Streamlit → upload Spotless CSV (we draft from public web research) → see customers table populated → trigger manual brief from UI → PDF generated
4. **Inngest dry-run**: Schedule cron for "5 min from now" → confirm function fires → confirm pipeline executes → confirm Postmark delivery
5. **Production cutover**: Monday 7am Chicago time, Spotless (or E&K backup) receives real brief in inbox; Stripe charges $149; Langfuse shows trace; cost auto-populated in `pipeline_runs`
6. **Feedback loop**: Click thumbs-down on a lead in PDF → row appears in `lead_feedback`

## Execution Shape (Workflow + /goal)

- **Phase 1 (parallel)**: schema migration first (10 min) → then scheduler wiring + Stripe wiring + Streamlit shell — independent surfaces
- **Phase 2 (parallel)**: monitoring + outreach copy + feedback API — independent
- **Phase 3 (serial)**: per-client pipeline refactor → e2e smoke
- **Phase 4 (serial)**: outreach send → demo → onboard pilot → first real Monday

Each phase = workflow run with agent fan-out per component.

## Task Tracker

10 tasks created in session (#1–#10). See TaskList for IDs and status.

## Out of Scope (v2)

- Multi-metro (Austin, Seattle, Denver) — stays Chicago-only
- Splink threshold UI tuning
- Owner-side full portal (scoring sliders, history charts)
- Vertical expansion (POS, security, signage, MSP)
- react-email migration — stays Jinja2 + WeasyPrint

## Risks

- **No real Spotless customer book yet** — pilot blocked until they share CSV. Mitigation: cold-call with sample brief built from Chicago Socrata + Spotless's public client list scraped from their website.
- **Stripe webhook latency in test** — use Stripe CLI `stripe listen` for local dev
- **Inngest cron timezone** — must be `America/Chicago` not UTC; explicit in cron config
- **Postmark sender reputation** — fresh domain warmup needed before broadcast; use shared stream first week
