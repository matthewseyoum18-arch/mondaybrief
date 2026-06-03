# MondayBrief

Monday-morning lead brief for commercial cleaners. Ingests Chicago open data → finds buildings opening near your existing routes → ships a PDF to your inbox at 7am Monday.

## Pipeline

```
permits (Socrata) → geocode → drive-time → H3 territory → Splink dedup
   → pgvector similarity → Claude scoring (Instructor) → WeasyPrint PDF → Resend send
```

Every step is one OSS repo doing one specific job. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the per-repo map.

## v1 e2e quickstart

1. Apply schema:

   ```bash
   psql $DATABASE_URL -f schema.sql
   ```

2. Set env vars from `.env.example`. New in v1:

   - `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`
   - `INNGEST_SIGNING_KEY`, `INNGEST_EVENT_KEY`
   - `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`
   - `MAGIC_LINK_SECRET`
   - `FEEDBACK_TOKEN_SECRET`
   - `APP_BASE_URL`

3. Seed clients:

   ```sql
   INSERT INTO clients (slug, name, contact_email)
   VALUES ('spotless','Spotless Cleaning Chicago','owner@spotlesschicago.com');
   ```

4. Start Inngest dev server + FastAPI:

   ```bash
   uvicorn mondaybrief.inngest.server:app --port 8288
   npx inngest-cli@latest dev -u http://localhost:8288/api/inngest
   ```

5. Start the Streamlit dashboard:

   ```bash
   streamlit run src/mondaybrief/ui/streamlit_app.py
   ```

6. Manual run (legacy slug path):

   ```bash
   python scripts/run_pipeline.py --client ek --offline
   ```

7. Client-uuid-driven path (canonical v1):

   ```bash
   python -c "from mondaybrief.pipeline import run; print(run(client_id='<uuid>'))"
   ```

## Verification

End-to-end checkpoints from `e2e v1.md`:

1. **Local smoke** (offline mode): `python scripts/run_pipeline.py --client-id=spotless --offline` → PDF in `output/` with real Chicago Socrata fixture data
2. **Stripe sandbox**: Test card 4242 → subscription created → webhook fires → client row marked active
3. **Onboarding flow**: Open Streamlit → upload Spotless CSV (we draft from public web research) → see customers table populated → trigger manual brief from UI → PDF generated
4. **Inngest dry-run**: Schedule cron for "5 min from now" → confirm function fires → confirm pipeline executes → confirm Postmark delivery
5. **Production cutover**: Monday 7am Chicago time, Spotless (or E&K backup) receives real brief in inbox; Stripe charges $149; Langfuse shows trace; cost auto-populated in `pipeline_runs`
6. **Feedback loop**: Click thumbs-down on a lead in PDF → row appears in `lead_feedback`

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # fill in real API keys
psql $DATABASE_URL -f schema.sql
python -m scripts.run_pipeline --client ek --offline
```

`--offline` runs the entire pipeline against fixture data (no network, no API spend) and writes the PDF to `out/brief_ek_<date>.pdf`.

Drop `--offline` to hit the real Chicago Socrata feed, real Geocodio, real Claude API.

## PDF rendering needs the GTK runtime

WeasyPrint shells out to native GTK/Pango/Cairo libraries at PDF render time.
Without them the pipeline raises `OSError: cannot load library 'libgobject-2.0-0'`
at the final step (and `test_pipeline_smoke` skips instead of writing a PDF).

- **Windows:** `winget install --id tschoonj.GTKForWindows -e` — installs the
  WeasyPrint-recommended GTK3 runtime to `C:\Program Files\GTK3-Runtime Win64\bin`
  and adds it to the system PATH. **Restart your shell** afterward so the new
  PATH is picked up.
- **Debian/Ubuntu (and the deploy image):** `apt-get install -y libpango-1.0-0
  libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev libcairo2`
- **macOS:** `brew install pango`

## Repos used

| Step | Repo | License | What it does |
|---|---|---|---|
| 1. Pull new permits | [`afeld/sodapy`](https://github.com/afeld/sodapy) | MIT | Chicago Socrata client |
| 2. Find each address | [`Geocodio`](https://www.geocod.io/) | proprietary (free tier) | Geocode addresses to lat/lng |
| 3. Drive-time to routes | [Mapbox Matrix API](https://docs.mapbox.com/api/navigation/matrix/) | proprietary (free tier) | Off-peak drive minutes |
| 4. Skip your customers | [`moj-analytical-services/splink`](https://github.com/moj-analytical-services/splink) | MIT | Probabilistic record linkage |
| 5. Inside service area | [`uber/h3-py`](https://github.com/uber/h3-py) | Apache-2.0 | Hexagonal spatial index |
| 6. Rank by money + fit | [`567-labs/instructor`](https://github.com/567-labs/instructor) + [Anthropic SDK](https://github.com/anthropics/anthropic-sdk-python) | MIT | Typed Claude output |
| 7. Verify owner phones | [Twilio Lookup](https://www.twilio.com/lookup) | proprietary (1¢/lookup) | Carrier check |
| 8. Build Monday PDF | [`Kozea/WeasyPrint`](https://github.com/Kozea/WeasyPrint) + [`pallets/jinja`](https://github.com/pallets/jinja) | BSD-3 | HTML → PDF |
| 9. Email it | [`resend/react-email`](https://github.com/resend/react-email) + [Resend](https://resend.com/) | MIT + proprietary | Transactional send (Svix-signed webhooks) |

Also wired: [`pgvector/pgvector`](https://github.com/pgvector/pgvector) (PostgreSQL License) + [`UKPLab/sentence-transformers`](https://github.com/UKPLab/sentence-transformers) (Apache-2.0) for similarity search against the cleaner's customer book.

## Scheduler — Inngest cron

The weekly Monday 7am Chicago trigger runs through [Inngest](https://www.inngest.com/). Two functions are registered:

| Function | Trigger | What it does |
|---|---|---|
| `pipeline.weekly-brief` | cron `TZ=America/Chicago 0 7 * * 1` | Reads active rows from `clients` table, fans out one `pipeline.run.requested` event per client |
| `pipeline.run` | event `pipeline.run.requested` | Calls `mondaybrief.pipeline.run(client_id=...)` inside `step.run` (auto-retries), Postmark-alerts `OPERATOR_EMAIL` on exception |

### Env vars

```bash
INNGEST_SIGNING_KEY=signkey-prod-...    # blank in local dev
INNGEST_EVENT_KEY=...                   # blank in local dev
OPERATOR_EMAIL=ops@mondaybrief.app      # where failure alerts go
```

### Local dev

```bash
# 1. install + start the FastAPI app that exposes /api/inngest
pip install -r requirements.txt
uvicorn mondaybrief.inngest.server:app --reload --port 8288

# 2. in a second terminal, start the Inngest Dev Server (npx — no install)
npx inngest-cli@latest dev -u http://localhost:8288/api/inngest

# 3. open the Inngest dashboard
open http://localhost:8288        # FastAPI healthz at /healthz
open http://localhost:8288/api/inngest  # Inngest handler
# Inngest Dev UI usually runs at http://localhost:8288 → http://localhost:8288/dev
```

From the Inngest Dev UI you can:

- Trigger `pipeline.weekly-brief` manually (don't wait for Monday)
- Send a synthetic `pipeline.run.requested` event with `{"client_id": "ek"}`
- Inspect step-level retries + failure traces

### Production

Point Inngest Cloud at the deployed `/api/inngest` URL, set `INNGEST_SIGNING_KEY` + `INNGEST_EVENT_KEY` in the deployment environment, and the Monday 7am cron is live.

## License

Code in this repo: MIT (see LICENSE). Third-party data (Chicago Open Data, OpenStreetMap via Geocodio) carries its own attribution requirements — see ODbL footer on every PDF brief.
