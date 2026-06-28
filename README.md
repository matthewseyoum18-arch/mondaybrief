# MondayBrief

MondayBrief is a GTM workflow system for local B2B service companies.

It turns public city signals into a weekly sales brief: new businesses opening,
permits being filed, nearby opportunities, route fit, lead scores, and call
openers. The goal is simple: help a seller know **who to contact, why now, what
to say, and how to improve the targeting over time**.

The current vertical is commercial cleaning. A cleaner can upload a customer
book, run the pipeline against public city data, and get a Monday-morning brief
of nearby accounts worth calling.

## Why It Exists

Local B2B service companies usually learn about new opportunities too late.

A new clinic, office, gym, cafe, or retail location may need cleaning before it
fully opens. But by the time a seller notices it, the buyer might already have a
vendor. MondayBrief is built around that timing gap.

Instead of generic lead scraping, the system asks:

- Which public signals suggest a business may need service soon?
- Is the opportunity near existing routes or customers?
- Does it look like the right kind of account?
- What is the practical reason to call?
- What feedback should improve the next brief?

## What The Product Does

MondayBrief converts messy public data into a repeatable outbound workflow.

| Stage | What Happens | Why It Matters |
|---|---|---|
| Signal ingestion | Pulls public records such as business licenses, permits, and local datasets | Finds companies before they are obvious cold-call targets |
| Cleaning and normalization | Standardizes names, addresses, categories, and location data | Makes public data usable for sales work |
| Geocoding and territory matching | Converts addresses into coordinates and checks proximity to existing customers | Prioritizes accounts a seller can actually service |
| Deduplication | Filters out known customers or duplicate records | Keeps the brief focused on net-new opportunities |
| Scoring | Ranks accounts by fit, route relevance, category, and estimated opportunity value | Helps the seller spend time on the best leads first |
| Narrative generation | Produces human-readable reasons, call openers, and evidence | Turns raw data into sales action |
| PDF / email delivery | Renders a weekly brief that can be sent to a customer inbox | Makes the workflow repeatable |
| Feedback loop | Captures thumbs-up/down style feedback on leads | Lets future scoring improve from seller judgment |

## Example Use Case

A commercial cleaner already services several medical offices and fitness
studios in Chicago.

MondayBrief can look at new public filings and identify that a nearby dental
clinic, wellness studio, or office build-out appears within the service radius.
The brief can then explain:

- the account name and address
- why it looks relevant
- which nearby existing customer makes it route-efficient
- what kind of opening signal triggered the recommendation
- a short call opener the seller can use

The output is not just "here is a lead." It is closer to:

> This account looks worth calling because it is a new medical-office signal
> near your existing route. You already service similar accounts nearby, so the
> opening angle is specific rather than generic.

## Product Workflow

```text
Public city signals
  -> ingest and normalize records
  -> geocode addresses
  -> match against customer book and territory
  -> dedupe known accounts
  -> score fit and opportunity
  -> generate sales narrative
  -> render PDF brief
  -> deliver and collect feedback
```

## Architecture

The application code lives in [`mondaybrief/`](mondaybrief/).

Key modules:

- `ingest`: pulls public city data and fixture-backed signal data
- `enrich`: geocoding, drive-time, territory, and similarity helpers
- `dedup`: record linkage so current customers and duplicates are removed
- `score`: deterministic scoring, profile logic, and narrative generation
- `render`: HTML/PDF brief templates
- `send`: email delivery and suppression logic
- `onboard`: customer CSV upload and validation
- `ui`: Streamlit demo and customer-facing pages
- `billing`, `auth`, `inngest`, `observability`, `feedback`: production-style
  pieces for subscription, login, scheduling, monitoring, and feedback loops

## Recruiter-Safe Demo

The public demo path uses fixtures only. It does not require login, a database,
API keys, Stripe, Resend, Inngest, or customer data.

```bash
cd mondaybrief
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts/run_pipeline.py --client ek --offline
streamlit run src/mondaybrief/ui/streamlit_app.py
```

In the Streamlit app, choose **View sample brief** on the sign-in screen.

Expected demo behavior:

- reads bundled fixture data
- runs the offline lead pipeline
- shows route and dedupe counts
- scores a small set of leads
- exposes a downloadable PDF brief

To verify the no-key path:

```bash
cd mondaybrief
python -m pytest tests/test_demo_polish.py tests/test_pipeline_smoke.py -q
```

## Production-Style Pieces

MondayBrief is structured like a product, not just a script:

- customer onboarding through CSV upload
- per-client pipeline runs
- weekly scheduling with Inngest
- subscription hooks through Stripe
- transactional email delivery
- PDF brief generation
- run-cost and delivery observability
- lead feedback capture
- offline fixture path for safe demos and tests

Some integrations need environment variables and account setup. The offline demo
is the safest way to inspect the product quickly.

## Why This Is A GTM Engineering Project

MondayBrief sits between sales work and software work.

The system has to understand messy public data, customer context, territory
constraints, lead quality, seller workflow, and messaging. The useful output is
not the dataset itself. The useful output is a repeatable sales action that a
person can trust enough to call.

That makes it a practical example of:

- source discovery and ingestion
- lead enrichment
- account scoring
- workflow automation
- sales-ready messaging
- feedback loops
- productized GTM operations

## Current Status

This is a portfolio/demo-stage project with a working offline path and
production-style modules. The fixture-backed demo is safe to run publicly. Live
production use requires real customer data, API credentials, sending
infrastructure, billing setup, and deployment configuration.

## Repository Notes

- Main application code: [`mondaybrief/`](mondaybrief/)
- Public proof link: <https://github.com/matthewseyoum18-arch/mondaybrief>
- Package metadata declares MIT licensing, but a root `LICENSE` file still
  should be added before treating the repo as formally licensed.

## README Research Notes

This README follows common guidance from:

- [GitHub Docs on repository READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes)
- [Make a README](https://www.makeareadme.com/)
- [GitHub README guide example](https://github.com/banesullivan/README)

The front page is intentionally structured around what the project is, why it is
useful, how it works, how to run it, and what is safe to demo.
