# MondayBrief — Implementation Plan
Created: 2026-05-31

Backlinks: [[output/OSS Repos License Audit for MondayBrief]] · [[output/MondayBrief Deep Research v1]] · [[output/Production Stack Research]]

---

## Goal
Stand up a runnable Python service that ingests Chicago open data, dedups against a cleaner's customer book, scores the survivors, builds a PDF, and emails it Monday 7am. Zero ops cost up to 10 clients.

## Stack Decision — Python monorepo
- Single language for the entire pipeline. Easier for a solo build than splitting Python (data + LLM) and Node (email rendering).
- `react-email` lives in `emails/` as a TypeScript subproject. Build once with `npx react-email export` → static HTML files that Python reads at send time. No Node runtime needed in production.
- Database: Neon Postgres with pgvector extension. One database, two schemas (`raw` for ingest, `app` for scored output + customer book).
- Secrets: `.env` file locally, Vercel/Fly/Railway env vars in prod. Never committed.

## File Layout
```
mondaybrief/
├── IMPLEMENTATION_PLAN.md       this file
├── README.md                    setup + run instructions
├── pyproject.toml               package metadata
├── requirements.txt             pinned deps
├── .env.example                 secret skeleton
├── .gitignore
├── schema.sql                   Postgres + pgvector DDL
├── fixtures/
│   ├── ek_customers.csv         E&K's 47-account customer book (sample)
│   └── sample_permits.json      mocked Socrata response (offline dev)
├── src/mondaybrief/
│   ├── config.py                Pydantic settings from env
│   ├── db.py                    Postgres connection + pgvector helpers
│   ├── models.py                Pydantic models for Lead, Customer, ScoredLead
│   ├── ingest/socrata.py        sodapy → Chicago datasets
│   ├── enrich/geocode.py        Geocodio API client
│   ├── enrich/territory.py      Uber H3 hex tagging
│   ├── enrich/similarity.py     sentence-transformers + pgvector RAG
│   ├── dedup/splink_match.py    Splink probabilistic linkage
│   ├── score/claude_score.py    Instructor + Anthropic Claude
│   ├── render/pdf.py            WeasyPrint HTML → PDF
│   ├── render/templates/brief.html  Jinja2 PDF template
│   ├── send/postmark.py         Postmark transactional send
│   └── pipeline.py              main orchestrator
├── emails/
│   ├── package.json             react-email project
│   └── MondayBrief.tsx          email wrapper around PDF
├── scripts/
│   └── run_pipeline.py          CLI entrypoint
└── tests/
    └── test_pipeline_smoke.py   end-to-end smoke test with fixtures
```

## Implementation Order
1. **Foundation** (no external services needed): models.py, config.py, db.py, schema.sql, fixtures.
2. **Ingest + enrich** (online deps): socrata.py → geocode.py → territory.py.
3. **Dedup**: splink_match.py against `ek_customers.csv`.
4. **RAG + score**: similarity.py builds embeddings + pgvector storage; claude_score.py runs the structured scoring + opener.
5. **Render + send**: WeasyPrint PDF from Jinja2 template; Postmark API call with the PDF attached and react-email-rendered HTML body.
6. **Orchestrator + CLI**: pipeline.py + scripts/run_pipeline.py for `python -m scripts.run_pipeline --client ek --dry-run`.
7. **Smoke test**: tests/test_pipeline_smoke.py runs the entire pipeline against fixtures, no network.

## Repo → File Map
| Repo | File | What it does in the pipeline |
|---|---|---|
| `sodapy` | `ingest/socrata.py` | Pulls every new Chicago business license, building permit, liquor application |
| `geocodio-library-python` | `enrich/geocode.py` | Forward-geocodes addresses to lat/lng |
| `uber/h3-py` | `enrich/territory.py` | Tags every customer + lead with H3 cell at resolution 9 |
| `pgvector` + `sentence-transformers` | `enrich/similarity.py` | Embeds each lead, finds closest existing customer in the cleaner's book |
| `moj-analytical-services/splink` | `dedup/splink_match.py` | Drops leads that are already customers or recently lost bids |
| `567-labs/instructor` + Anthropic SDK | `score/claude_score.py` | Forces Claude's reply into typed `ScoredLead { score, opener, margin_est, why }` |
| `Kozea/WeasyPrint` + `Jinja2` | `render/pdf.py` + `templates/brief.html` | Builds the 3-page PDF brief |
| `resend/react-email` | `emails/MondayBrief.tsx` | Builds the email wrapper that delivers the PDF |
| Postmark API | `send/postmark.py` | Transactional send with separate Message Stream |
| (later) `langfuse` | optional in `claude_score.py` | Trace every Claude call |
| (later) `pg-boss` | optional in `pipeline.py` | Replaces Inngest cron when usage grows past free tier |

## License Compliance
Every dependency MIT / Apache-2.0 / BSD / PostgreSQL License. ODbL attribution required because Geocodio + future OSRM hit OSM data — added as footer line in the PDF template.

## Local Dev Setup
```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # then fill in real keys
psql $DATABASE_URL -f schema.sql
python -m scripts.run_pipeline --client ek --offline    # uses fixtures, no network
```

## Deploy Path
1. Local dev → ensure smoke test passes.
2. Deploy Python service to Fly.io or Railway ($5/mo).
3. Neon Postgres Launch tier ($19/mo).
4. Postmark Basic ($15/mo).
5. Cron: keep Inngest free tier until ~50k executions/mo, then swap to pg-boss inside the existing Postgres.

## Risks + Open Items
- **Chicago Socrata pagination**: limits to 50k rows/request — fine for weekly delta, but verify SODA3 cursor behavior before commit.
- **Splink threshold tuning**: needs real customer data to calibrate match_weight cutoff. Ship with a conservative threshold and lower based on cleaner feedback.
- **WeasyPrint Docker deps**: requires Pango + Cairo + libffi in production image. Document the Dockerfile install step.
- **react-email build step**: requires Node 20 at build time but not runtime. Acceptable.
- **Claude prompt cache**: configure `cache_control: { type: "ephemeral" }` on the scoring rubric so 90% cache hit kicks in by week 2.
