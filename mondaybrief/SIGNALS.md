# MondayBrief — Signal Taxonomy

What signals identify a good-fit lead for a commercial cleaner. v1 ships
with three signal classes: `new_opening`, `expansion`, `churn_intent`.
Plus negative-signal disqualification.

Detection lives in `src/mondaybrief/score/signals.py` (deterministic, no
LLM).

> **Scoring change (2026-06-01):** signal class is no longer a multiplier
> on an opaque Claude score. It is now one of five **components** in the
> deterministic per-client engine (`score/engine.py`), weighted by the
> client's profile. See "Scoring impact" below and the
> [[output/MondayBrief Signal & Scoring System]] report. `signals.py`'s
> `classify_signal` is unchanged and remains the single source of truth
> for the class.

---

## 1. NEW OPENING (vendor selection window 30-90d)

**Why it converts**: business hasn't picked a janitorial vendor yet.
Owner is actively shopping or about to be. Highest meeting-book rate.

**v1 sources (wired)**:

| Source ID | Dataset | Detection | Lead-time |
|---|---|---|---|
| `r5kz-chrr` | Chicago Business Licenses | `application_type='ISSUE'` AND `license_status='AAI'` | 30-60d before doors open |
| `4ijn-s7e5` | Chicago Liquor / Public Amusement | new application | 60-120d before opening |
| `ydr8-5enu` | Chicago Building Permits | `permit_type='PERMIT - NEW CONSTRUCTION'` OR `permit_type='PERMIT - SIGNS'` | 30-180d |
| `nyc:ic3t-wcy2` | NYC DCWP Legally Operating Businesses | `license_status='Active'` AND first-issue date inside window | 30-90d |
| `nyc:ipu4-2q9a` | NYC DOB Now Approved Permits | `job_type='NB'` AND `filing_status='P'` | 60-180d |

**v1.1 sources (deferred, documented)**:

- **Certificate of Occupancy** — final step before tenant moves in. Chicago publishes via `kdd2-fhe6` (Building Permit Inspections); NYC via `dec5-h7g3` (DOB CO Issuance). Add `ingest/co_issuance.py`.
- **Health Department food service permits** — restaurants 30-45d before opening. Chicago via `4ijn-s7e5` already. NYC via `ph7v-u5f3` (DOHMH Food Service Establishment).
- **DBA / Fictitious name filings** — Cook County Clerk via OpenSearch; NY Dept of State via `cnym-pjg9`. Earliest legal signal.
- **Sign permits** — sub-filter of building permits. Already in Chicago `ydr8-5enu` via `permit_type='PERMIT - SIGNS'`. Surface separately.
- **New commercial leases** — CoStar paid; LoopNet free for "recently leased". Difficult ToS terrain.

**Negative-signal exclusions wired**:
- Demolition permits (`PERMIT - WRECKING/DEMOLITION`, NYC `DM`)
- Revoked / cancelled (`REV`, `AAC`)
- NYC withdrawn / closed (`Q`, `W`, `X`)

---

## 2. EXPANSION (existing biz adding sqft / locations / headcount)

**Why it converts**: cleaner ALREADY has a vendor but needs to absorb
new sqft. Either upsell expansion to incumbent, or replace if incumbent
can't scale. Warm lead, but vendor-incumbent advantage on prospect side.

**v1 sources (wired)**:

| Source | Detection | Lead-time |
|---|---|---|
| Chicago `ydr8-5enu` | `permit_type='PERMIT - RENOVATION/ALTERATION'` | 30-120d |
| NYC `nyc:ipu4-2q9a` | `job_type in ('A1','A2','A3')` | 30-120d |
| NYC `nyc:ic3t-wcy2` | DCWP amendment adding new location_id | event-time |

**v1.1 sources (deferred)**:

- **Headcount spikes** — LinkedIn employee-count delta >20% in 90d. Scrape via LinkedIn API (Tier 2 dev) or proxy via Greenhouse / Workable job posting count. ToS-risky.
- **Funding rounds** — Crunchbase API ($499/mo) or free RSS from TechCrunch / Crain's Chicago / NY Business Journal.
- **News announcements** — Google News API filtered by "new location" + ICP keywords.
- **M&A activity** — acquirer often renegotiates vendors at 90d. PitchBook ($) or Crunchbase free tier.
- **OSHA / EPA inspection schedules** — sometimes triggers re-cleaning contracts.

---

## 3. CHURN INTENT (existing biz unhappy with current cleaner)

**Why it converts**: warmest lead in B2B services. Active dissatisfaction
= ready to switch. Hardest to detect — requires external signals not on
city permit feeds.

**v1 sources (slot wired, ingest deferred to v1.1)**:

| Source | Detection | Lead-time | Notes |
|---|---|---|---|
| RFP boards (BidNet, GovTribe, BidPrime) | "janitorial services RFP" keyword match | 14-30d | $$ paid APIs; OR free public-bid RSS |
| Yelp review keyword drop | 90d rolling avg of "dirty"/"smelly"/"unclean" mentions | event-time | Yelp Fusion API free tier 5k/day |
| Google Reviews | same keyword detection | event-time | Google Places API ~$17/1k reviews |
| BBB complaints | new complaint filed against existing cleaner | event-time | BBB scraping ToS-risky; consider partnership |
| Court records | lawsuit filed against current cleaning vendor | event-time | Cook County clerk + NY PACER, scrape-friendly |
| Glassdoor | "facility is dirty" mention in reviews | event-time | Glassdoor scraping is ToS-disallowed; skip |
| In-house janitor job posting | counter-signal — they're going internal | event-time | Indeed / LinkedIn — DROP from brief if detected |

**Detection module**: tag the synthetic `RawLead.source` with prefix
`rfp_board:`, `review_drop:`, `bbb:` so `classify_signal` routes to
`churn_intent` automatically. The classifier code is wired today.

---

## Negative signals (drop from brief — already wired)

| Signal | How detected |
|---|---|
| Bankruptcy | `raw_json.bankruptcy = True` (future court ingest) |
| Foreclosure | property records lookup (deferred) |
| Business closed | license_status `AAC` / NYC filing_status `Q`/`W`/`X` |
| Vendor lock-in | multi-year contract in news / case filings (deferred) |
| Demolition | `permit_type='PERMIT - WRECKING/DEMOLITION'` / NYC `job_type='DM'` |
| Owner indictment | future court-records ingest |

---

## Ranked priority for next ingest work (top 5)

1. **Certificate of Occupancy** (Chicago + NYC) — strongest new_opening signal, ~30d before move-in. Free public data.
2. **RFP boards** — strongest churn_intent signal. Belkins / CIENCE etc charge $50-200 per qualified lead; free public-bid RSS gives most of the signal.
3. **Yelp Fusion API** — review keyword drops, 5k/day free tier.
4. **Crunchbase funding RSS** — expansion signal. Free RSS.
5. **DBA filings** (Cook County + NY DOS) — earliest legal new_opening signal.

---

## Scoring impact

Signal class is one **component** (0-10) in the deterministic per-client
engine (`score/engine.py`), via `economics.SIGNAL_CLASS_STRENGTH`:

| Class | Strength (0-10) | Why |
|---|---|---|
| new_opening | 10.0 | Vendor selection NOW |
| churn_intent | 9.0 | Already shopping, but incumbent friction |
| expansion | 7.0 | Vendor already in place, harder displacement |
| unknown | 5.0 | Lack of signal = lower confidence |
| disqualified | 0.0 | Dropped before scoring (`score_many` filters) |

The engine combines this with margin, route, category, and timing under the
client's weight vector, then maps to an A/B/C/drop tier. The old
multiplicative `SIGNAL_CLASS_MULTIPLIER` in `signals.py` is retained but no
longer applied in scoring (kept for reference). Calibrate the strengths and
per-client weights after ~4 weeks of `lead_feedback` shows actual close
rates per class.

---

## Dataset identity flag — `4ijn-s7e5` (RESOLVED 2026-06-02)

`4ijn-s7e5` is the Chicago **Food Inspections** dataset, NOT liquor. The v2
signal layer (`score/detectors.py::detect_chicago_food_inspection`) now treats a
row with `inspection_type='License'` as a strong new-opening signal
(`chi_food_license`, precision 0.88) — a brand-new food establishment about to
open. Routine inspection types (Canvass/Complaint) emit no signal. Legacy
liquor-shaped fixture rows (no `inspection_type`) are still read as a liquor
new-issuance for back-compat. Chicago liquor / Public Place of Amusement proper
lives at **`nrmj-3kcf`** — a detector slot exists (`detect_chicago_liquor`) and
goes live once that ingest module lands. The `ingest/socrata.py` DATASETS entry
should be migrated to the food-inspection schema (`inspection_type`,
`inspection_date`) in the next ingest sprint.

---

## v2 signal-fusion layer (2026-06-02)

This taxonomy now feeds a calibrated **fusion** layer, not a flat per-class
strength. See vault note **"MondayBrief Signal Fusion System v2"** and
`score/signal_layer.py` + `score/detectors.py`. In short: each record becomes a
`Signal` with a precision prior + decay; signals on the same business (name AND
address) fuse via log-odds (corroboration lifts, contradiction vetoes); a 0.40
confidence floor suppresses weak leads. `classify_signal` is retained for the
coarse class label. The signal classes and sources documented above are
unchanged — they are now scored by confidence, not assumed.

---

## v1.5 ranked source backlog (research 2026-06-01)

All FREE + low-ToS. One file per source under `ingest/`, wired into
`pipeline._METRO_INGEST`. Deferred this cycle (we focused on per-client
scoring), documented here for the next ingest sprint.

| # | Source | Metro | Dataset / endpoint | Class | Lead-time | ToS |
|---|---|---|---|---|---|---|
| 1 | Certificate of Occupancy | NYC | `pkdm-hqz6` (Socrata) | new_opening | 0-30d | low |
| 2 | Janitorial RFP / bid boards | both | DemandStar (free), SAM.gov (API), NYC PASSPort/CROL (RSS) | churn_intent | 60-120d | low |
| 3 | Food-service establishment permits | NYC | `43nn-pn8j` (status "applied, not inspected") | new_opening | 30-45d | low |
| 4 | Eviction filings (vendor-switch proxy) | NYC | `6z8x-wfk4` | churn_intent | 30-90d | low |
| 5 | Parcel sales / property transfers | Chicago | Cook County `wvhk-k5uv` | expansion | 30-90d | low |
| 6 | DBA / assumed-name filings | both | Cook County Clerk + NY DOS (web-only, no API) | new_opening | 60-120d | low |
| — | Chicago CoO | Chicago | **not published** — no public dataset | — | — | — |

**Excluded (high ToS — do NOT scrape):** Yelp, Google reviews, LinkedIn,
Glassdoor. churn_intent in v1.1 uses public RFP boards + evictions only.
