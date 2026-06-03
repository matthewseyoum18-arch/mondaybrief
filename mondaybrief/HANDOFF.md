# MondayBrief — Session Handoff (2026-06-01)

State snapshot before hitting session limit. Read this first next session.

---

## SESSION 2026-06-02 — v2 SIGNAL-FUSION SYSTEM (read this first)

Built the calibrated signal-fusion layer. Flow is now **detect → fuse → suppress → score**. Full design: vault note `output/MondayBrief Signal Fusion System v2.md`.

**Done (all green, 81 passed / 1 skipped, py_compile clean):**
- `score/signal_layer.py` — `Signal` type, entity resolver (name AND address), weight-of-evidence, signal-type decay (coincident exp + leading ramp-then-decay), log-odds `fuse()` (MAX-within-family + SUM-across-family, hard veto, soft-damp).
- `score/detectors.py` — pure per-source detectors + `run_detectors`; resolves the `4ijn-s7e5` flag (Food Inspections, `inspection_type='License'`=opening@0.88).
- `score/economics.py` — v2 SIGNAL block: BASE_RATE 0.03, WOE_CLAMP, CONFIDENCE_FLOOR 0.40, PRECISION priors per signal_type, families.
- `score/engine.py` + `claude_score.py` — `signal_class_score` takes fused P (`10×P_fused`); `_score_with_fusion` groups by entity, fuses, collapses corroborated dups to one lead, applies the floor. Flag-gated `settings.signal_layer_enabled` (default True); flag-off == v1 exactly (pinned by `test_flag_parity.py`).
- `models.py` — ScoredLead += `signal_confidence`, `corroboration_count`, `signal_evidence`.
- Tests: `test_signal_layer.py`, `test_detectors.py`, `test_flag_parity.py` (37 new).
- 5 product decisions locked (see vault note §1). Adversarial review (21 agents) → 14 findings, all fixed or documented.

**Deferred (DB/provisioning-blocked, documented, layer runs offline on seeded constants):**
- `schema.sql` migration (`signal_confidence`/`source`/`corroboration_count`/`suppressed_reason` cols + `signal_priors` table); persist source + confidence in `_insert_scored_lead`.
- `scripts/update_signal_priors.py` nightly Beta-Binomial update from `lead_feedback`.
- Ingest the un-wired sources the detectors already handle (inert until then): Chicago liquor `nrmj-3kcf`, NYC CO `pkdm-hqz6`, NYC food `43nn-pn8j`, parcel `wvhk-k5uv`, eviction `6z8x-wfk4`, RFP boards (DemandStar/SAM/PASSPort/CROL). Migrate `ingest/socrata.py` `4ijn-s7e5` to the food-inspection schema.
- Render `signal_evidence` + tier on the PDF/email (decision #4: evidence in words, hide number).

**Known calibration tensions to watch (documented, by-design):**
- A bare-office `1006` license (no vertical keyword in the name) surfaces only while FRESH (≤~2d), then needs corroboration. Expansion-class lone signals (alteration/parcel/eviction, priors ≤0.40) never clear the floor alone by design — they corroborate. Re-fit floor + priors against real `lead_feedback` after ~4 weeks.
- Out-of-ICP government RFP (`rfp_govt` 0.15) is suppressed alone but could ride corroboration; inert today (source not ingested). Add a government gate if/when RFP ingest lands for Tier-1 clients.

---

## What's done

### e2e v1.md (code-complete)
- 7/7 "What Gets Wired" sections landed (Scheduler, Onboarding, Billing, Dashboard, Monitoring, Outreach, Feedback)
- 6/6 "Critical Files to Modify" applied
- Multi-metro pipeline.run() fan-out (Chicago + NYC via `_METRO_INGEST`)
- 43 .py files, 3941 LOC under `src/mondaybrief`, all `py_compile` clean

### NYC v1 (code-complete)
- `ingest/nyc_socrata.py` with real dataset IDs (ipu4-2q9a DOB Now, ic3t-wcy2 DCWP)
- `fixtures/nyc_sample_permits.json` (18 rows)
- `fixtures/pritchard_customers.csv` (22 rows, Pritchard Industries LIC pilot)
- `outreach/nyc_cleaner_targets.csv` (13 NYC commercial cleaners)
- `tests/test_nyc_ingest.py`
- `NYC_README.md`

### Accuracy wins #1-5 (shipped, py_compile clean)
1. Few-shot calibration examples added to `SCORING_RUBRIC` in `score/claude_score.py` (3 anchors: great fit / borderline / trash)
2. `score/taxonomy.py` — deterministic license_code -> category mapping (Chicago + NYC); `pipeline._guess_category` delegates
3. Geocodio confidence filter (`GEOCODE_MIN_ACCURACY = 0.85`) in `enrich/geocode.py` — drops low-confidence geocodes before H3 assignment
4. Socrata WHERE filter in both `ingest/socrata.py` (Chicago: AAI + ISSUE only, TI/new construction permits) and `ingest/nyc_socrata.py` (NYC: NB/A1/A2/A3 job types, license_status Active)
5. Feedback-loop prompt block: `_feedback_summary` in `score/claude_score.py` reads last 28 days `lead_feedback` per client, injects as 3rd cached system block

### Automation (scripted, runnable)
- `mondaybrief/main.py` — FastAPI host wrapper (Stripe + Postmark + Feedback routers + healthz + landing)
- `scripts/bootstrap.ps1` / `bootstrap.sh` — venv + pip install + schema apply + secrets + py_compile
- `scripts/gen_secrets.py` — idempotent local secret generation (already ran, .env populated)
- `scripts/seed_clients.py` — upsert Spotless / Pritchard / EK pilot rows
- `scripts/PROVISIONING.md` — manual provisioning checklist (Stripe / Postmark / Langfuse / Inngest / Neon signups + DNS + warmup)

### Memory updates
- `project_mondaybrief_nyc_v1.md` — NYC added to v1
- `project_mondaybrief_icp_signals.md` — Tier 1/2 ICP, 3 signal classes equally weighted, optimize meeting-book rate
- `MEMORY.md` index updated

---

## What's paused (resume next session)

### Deep-research workflows
Both stopped mid-execution. Resume via `Workflow({resumeFromRunId: "..."})` — cached agents skip on restart, only new work runs.

| Workflow | Task ID | Run ID | Topic |
|---|---|---|---|
| Signals | `w5chq2883` | `wf_26e12bc4-64e` | B2B sales signal taxonomy for cleaning — 3 signal classes × source / accuracy / lead-time / cost / ToS |
| Pricing | `wodn17oyf` | `wf_ae445531-5e7` | Janitorial SaaS pricing comparables (ServiceTitan, Jobber, Aspire, BidNet, etc.) |

---

## What's next (in order)

1. **Resume signals workflow** → synthesize report → pick top 5-7 signal detectors to wire next
2. **Implement detector modules** — `ingest/signals/` package with one file per signal class:
   - `signals/new_openings.py` (CO issuance, sign permits, food permits, liquor apps, DBA filings, trademark filings, lease records)
   - `signals/churn_intent.py` (review keyword drops, BBB complaints, RFP postings on BidNet/GovTribe, lawsuits)
   - `signals/expansion.py` (TI permits on existing addresses, multi-site DCWP amendments, headcount spikes, funding rounds, news of new locations)
3. **Resume pricing workflow** → lock $99 founders / $149 Tier 1 / $299 Tier 2 tiers (or revise based on comparables)
4. **Scoring rubric reweighting** — current rubric is margin(40) / route(30) / category(20) / timing(10). Per ICP memo wants: signal class(30) / margin(30) / route(25) / category(15). Edit `score/claude_score.py` SCORING_RUBRIC + add `signal_class` field to `ScoredLead` Pydantic model.
5. **Cold-call Spotless** to get real customer book CSV (only outreach blocker — `fixtures/spotless_customers.csv` is research placeholder per e2e v1.md Risks section)

---

## Blocked on external

- Stripe / Postmark / Langfuse / Inngest / Neon / Anthropic / Geocodio / Mapbox / Twilio signups (manual, ~30-45 min)
- Domain + DKIM/SPF/DMARC DNS
- Postmark deliverability warmup (~5 business days)
- Real Spotless / Pritchard customer book CSVs (outreach-gated)

See `scripts/PROVISIONING.md` for the full manual checklist.

---

## Quick resume commands

```powershell
# Verify everything still compiles
cd "C:\Users\ketty\OneDrive\Documents\gtm project\mondaybrief"
python -m py_compile (Get-ChildItem -Path "src\mondaybrief" -Filter "*.py" -Recurse).FullName

# Resume signals research
# (in Claude Code, prompt: "resume signals workflow wf_26e12bc4-64e")

# Run offline smoke test
python scripts\run_pipeline.py --client ek --offline
```
