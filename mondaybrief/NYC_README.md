## NYC v1 — quickstart

1. Confirm `clients.metros` includes 'nyc' for any NYC tenant row.
2. Seed a NYC pilot: `INSERT INTO clients (slug, name, contact_email, metros) VALUES ('pritchard','Pritchard Industries','contact@pritchardindustries.com',ARRAY['nyc']);`
3. Upload Pritchard customer book via Streamlit OR load fixture: `fixtures/pritchard_customers.csv`.
4. Run NYC-only ingest sanity: `python -c "from mondaybrief.ingest.nyc_socrata import load_fixture; print(len(load_fixture('fixtures/nyc_sample_permits.json')))"`
5. Full pipeline (once the pipeline.run multi-metro fan-out lands): `python -c "from mondaybrief.pipeline import run; print(run(client_id='<pritchard-uuid>'))"`

## Datasets in scope

- ipu4-2q9a — DOB Now: Build — Approved Permits (real, daily-refresh)
- ic3t-wcy2 — Legally Operating Businesses (DCWP licenses)
- TODO: NY State Liquor Authority — state-level dataset, not NYC Socrata, needs separate adapter

## NYC pilot pool

12 publicly-listed commercial cleaners in outreach/nyc_cleaner_targets.csv. Primary = Pritchard Industries (LIC HQ).

## Volume estimate

NYC delivers ~50-100 qualified leads/wk per cleaner cluster, vs 20-40 in Chicago. See [[project_mondaybrief_nyc_v1]] in memory.

## Integration TODO (post-build)

- pipeline.run() must read clients.metros and fan ingest across metro adapters, unioning RawLeads before H3/Splink/score.
- merge outreach/nyc_cleaner_targets.csv into outreach/targets_v1.csv after both workflows complete.
