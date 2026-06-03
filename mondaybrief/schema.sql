-- MondayBrief — Postgres + pgvector schema
-- Run once on a fresh Neon Launch instance:
--   psql $DATABASE_URL -f schema.sql

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ===== Customer book (uploaded by each cleaner) =====
CREATE TABLE IF NOT EXISTS customers (
  id          BIGSERIAL PRIMARY KEY,
  client_id   TEXT NOT NULL,                  -- e.g. 'ek', 'teamclean', 'sanitrol'
  name        TEXT NOT NULL,
  address     TEXT NOT NULL,
  city        TEXT NOT NULL,
  state       TEXT NOT NULL,
  lat         DOUBLE PRECISION,
  lng         DOUBLE PRECISION,
  h3_cell     TEXT,                            -- resolution-9 cell ID
  category    TEXT,                            -- 'dental', 'office', 'gym', etc.
  sqft        INTEGER,
  monthly_rev NUMERIC(10,2),                   -- for margin uplift math
  status      TEXT DEFAULT 'active',           -- 'active', 'lost', 'won', 'bid'
  embedding   vector(384),                      -- sentence-transformers BGE-base-en
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (client_id, address)
);

CREATE INDEX IF NOT EXISTS customers_client_idx ON customers (client_id);
CREATE INDEX IF NOT EXISTS customers_h3_idx     ON customers (client_id, h3_cell);
CREATE INDEX IF NOT EXISTS customers_embed_idx  ON customers USING hnsw (embedding vector_cosine_ops);

-- ===== Raw permits / licenses pulled from Chicago Socrata =====
CREATE TABLE IF NOT EXISTS raw_leads (
  id              BIGSERIAL PRIMARY KEY,
  source          TEXT NOT NULL,               -- 'r5kz-chrr' | 'ydr8-5enu' | '4ijn-s7e5'
  source_id       TEXT NOT NULL,               -- city's row id
  name            TEXT,
  dba             TEXT,
  address         TEXT,
  city            TEXT,
  state           TEXT,
  zip             TEXT,
  date_issued     DATE,
  raw_json        JSONB NOT NULL,
  ingested_at     TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (source, source_id)
);

CREATE INDEX IF NOT EXISTS raw_leads_date_idx ON raw_leads (date_issued DESC);

-- ===== Enriched + scored leads ready for the weekly PDF =====
CREATE TABLE IF NOT EXISTS scored_leads (
  id                  BIGSERIAL PRIMARY KEY,
  client_id           TEXT NOT NULL,
  week_of             DATE NOT NULL,
  raw_lead_id         BIGINT REFERENCES raw_leads(id),
  name                TEXT NOT NULL,
  address             TEXT NOT NULL,
  lat                 DOUBLE PRECISION,
  lng                 DOUBLE PRECISION,
  h3_cell             TEXT,
  category            TEXT,
  owner_name          TEXT,
  owner_phone         TEXT,
  owner_phone_valid   BOOLEAN,
  nearest_customer_id BIGINT REFERENCES customers(id),
  drive_minutes       NUMERIC(5,2),
  margin_est_monthly  NUMERIC(10,2),
  margin_uplift_pct   NUMERIC(5,2),
  score               INTEGER,
  why                 TEXT,
  opener              TEXT,
  sources             JSONB,                   -- {license_url, permit_url, maps_url}
  status              TEXT DEFAULT 'queued',   -- 'queued' | 'shipped' | 'approved' | 'rejected'
  feedback            TEXT,
  created_at          TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (client_id, week_of, address)
);

CREATE INDEX IF NOT EXISTS scored_leads_week_idx
  ON scored_leads (client_id, week_of DESC, score DESC);

-- Per-client scoring engine columns (2026-06-01). tier + the five component
-- sub-scores that produced `score`, so a brief is fully hand-auditable.
ALTER TABLE scored_leads ADD COLUMN IF NOT EXISTS tier           TEXT;     -- 'A'|'B'|'C'|'drop'
ALTER TABLE scored_leads ADD COLUMN IF NOT EXISTS margin_score   NUMERIC(4,2);
ALTER TABLE scored_leads ADD COLUMN IF NOT EXISTS route_score    NUMERIC(4,2);
ALTER TABLE scored_leads ADD COLUMN IF NOT EXISTS category_score NUMERIC(4,2);
ALTER TABLE scored_leads ADD COLUMN IF NOT EXISTS timing_score   NUMERIC(4,2);
ALTER TABLE scored_leads ADD COLUMN IF NOT EXISTS signal_score   NUMERIC(4,2);

-- ===== Pipeline run log =====
CREATE TABLE IF NOT EXISTS pipeline_runs (
  id            BIGSERIAL PRIMARY KEY,
  client_id     TEXT NOT NULL,
  week_of       DATE NOT NULL,
  started_at    TIMESTAMPTZ DEFAULT NOW(),
  finished_at   TIMESTAMPTZ,
  status        TEXT DEFAULT 'running',        -- 'running' | 'shipped' | 'failed'
  permits_pulled       INTEGER,
  geocoded             INTEGER,
  inside_area          INTEGER,
  after_dedup          INTEGER,
  scored               INTEGER,
  phones_validated     INTEGER,
  pdf_path             TEXT,
  postmark_delivery_id TEXT,
  cost_usd             NUMERIC(10,4),
  error                TEXT
);

-- =====================================================================
-- v1 e2e migration (2026-05-31)
--   Adds multi-tenant client registry, Stripe subscriptions, per-lead
--   feedback collection, and Postmark delivery telemetry. Existing
--   tables keep their TEXT client_id slug for backwards compat; a new
--   nullable client_uuid FK column is added alongside.
--
--   Backfill plan (run after seeding `clients` from CLIENT_PROFILES):
--     UPDATE customers     c SET client_uuid = cl.id
--       FROM clients cl WHERE cl.slug = c.client_id;
--     UPDATE pipeline_runs p SET client_uuid = cl.id
--       FROM clients cl WHERE cl.slug = p.client_id;
--     UPDATE scored_leads  s SET client_uuid = cl.id
--       FROM clients cl WHERE cl.slug = s.client_id;
--   Once verified non-null across all rows, a follow-up migration will
--   ALTER ... SET NOT NULL and drop the TEXT client_id column.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- --- new tables -------------------------------------------------------

CREATE TABLE IF NOT EXISTS clients (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug               TEXT UNIQUE NOT NULL,                  -- e.g. 'spotless', 'ek'
  name               TEXT NOT NULL,
  contact_email      TEXT NOT NULL,
  postmark_stream    TEXT NOT NULL DEFAULT 'outbound',
  metros             TEXT[] NOT NULL DEFAULT ARRAY['chicago'],
  stripe_customer_id TEXT,
  active             BOOLEAN NOT NULL DEFAULT true,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_clients_slug ON clients (slug);

CREATE TABLE IF NOT EXISTS subscriptions (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id              UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  stripe_subscription_id TEXT UNIQUE NOT NULL,
  status                 TEXT NOT NULL,                     -- active, past_due, canceled, etc.
  current_period_end     TIMESTAMPTZ,
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_client ON subscriptions (client_id);

CREATE TABLE IF NOT EXISTS lead_feedback (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scored_lead_id BIGINT NOT NULL REFERENCES scored_leads(id) ON DELETE CASCADE,
  client_id      UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  thumbs         TEXT NOT NULL CHECK (thumbs IN ('up','down')),
  note           TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lead_feedback_lead   ON lead_feedback (scored_lead_id);
CREATE INDEX IF NOT EXISTS idx_lead_feedback_client ON lead_feedback (client_id);

CREATE TABLE IF NOT EXISTS email_events (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scored_lead_id      BIGINT REFERENCES scored_leads(id) ON DELETE SET NULL,
  pipeline_run_id     BIGINT REFERENCES pipeline_runs(id) ON DELETE SET NULL,
  client_id           UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  event_type          TEXT NOT NULL,                        -- delivered | bounced | opened | spam_complaint | dunning_sent | etc.
  postmark_message_id TEXT,
  payload             JSONB,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_events_client          ON email_events (client_id);
CREATE INDEX IF NOT EXISTS idx_email_events_postmark_msg    ON email_events (postmark_message_id);
CREATE INDEX IF NOT EXISTS idx_email_events_pipeline_run    ON email_events (pipeline_run_id);

-- --- FK columns on existing tables -----------------------------------
-- Note: existing client_id columns are TEXT slugs; we add a parallel
-- nullable UUID FK called client_uuid. After backfill, application code
-- migrates to client_uuid and a later migration drops the TEXT column.

ALTER TABLE customers      ADD COLUMN IF NOT EXISTS client_uuid UUID REFERENCES clients(id);
ALTER TABLE pipeline_runs  ADD COLUMN IF NOT EXISTS client_uuid UUID REFERENCES clients(id);
ALTER TABLE scored_leads   ADD COLUMN IF NOT EXISTS client_uuid UUID REFERENCES clients(id);

CREATE INDEX IF NOT EXISTS idx_customers_client      ON customers (client_uuid);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_client  ON pipeline_runs (client_uuid);
CREATE INDEX IF NOT EXISTS idx_scored_leads_client   ON scored_leads (client_uuid);

-- =====================================================================
-- Per-client scoring profile (2026-06-01)
--   One row per client holding the tunable scoring config: component
--   weights, category preferences, contract floor, drive radius, and
--   category exclusions. Seeded from the customer book at onboarding
--   (score.profile.seed_from_book) and tuned from thumbs feedback
--   (score.profile.tune_from_feedback). JSONB so the weight/pref maps
--   evolve without migrations.
-- =====================================================================
CREATE TABLE IF NOT EXISTS client_profiles (
  client_id            TEXT PRIMARY KEY,                      -- slug, matches customers.client_id
  client_uuid          UUID REFERENCES clients(id) ON DELETE CASCADE,
  weights              JSONB NOT NULL DEFAULT '{}'::jsonb,    -- {margin,route,category,timing,signal_class}
  category_prefs       JSONB NOT NULL DEFAULT '{}'::jsonb,    -- {category: 0-10}
  min_contract_monthly NUMERIC(10,2) NOT NULL DEFAULT 0,
  max_drive_minutes    NUMERIC(6,2)  NOT NULL DEFAULT 15,
  exclusions           JSONB NOT NULL DEFAULT '[]'::jsonb,    -- ["restaurant","union"]
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_client_profiles_uuid ON client_profiles (client_uuid);

-- =====================================================================
-- Delivery hardening (2026-06-01)
--   CAN-SPAM suppression list + Stripe webhook idempotency + a run-week
--   index so the pipeline can cheaply check "did we already ship this
--   client's brief this week?" before sending again.
-- =====================================================================

-- Recipient-level suppression. A row here means we must NOT send to this
-- address. Fed by Postmark SpamComplaint / SubscriptionChange webhooks and
-- by the one-click unsubscribe route. Keyed on the lowercased email so
-- casing never lets a suppressed address slip through.
CREATE TABLE IF NOT EXISTS email_suppressions (
  email        TEXT PRIMARY KEY,                 -- always store lower(email)
  client_id    UUID REFERENCES clients(id) ON DELETE CASCADE,
  reason       TEXT NOT NULL,                    -- 'spam_complaint' | 'unsubscribe' | 'hard_bounce' | 'manual'
  source       TEXT,                             -- 'postmark_webhook' | 'unsubscribe_link' | 'admin'
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_suppressions_client ON email_suppressions (client_id);

-- Processed Stripe event IDs. The webhook inserts the event id before
-- handling it; ON CONFLICT DO NOTHING + rowcount==0 means "already seen,
-- skip" so a Stripe retry never double-applies a subscription change.
CREATE TABLE IF NOT EXISTS stripe_events (
  event_id     TEXT PRIMARY KEY,                 -- Stripe's evt_... id
  event_type   TEXT NOT NULL,
  received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cheap "already shipped this week?" lookup for run-level idempotency.
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_client_week
  ON pipeline_runs (client_uuid, week_of, status);

-- Run-level idempotency lock. Failed runs may be retried, but only one
-- running-or-shipped run may exist for a client/week. The online pipeline
-- inserts through this key before doing any email side effect.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_pipeline_runs_client_week_active_send
  ON pipeline_runs (client_uuid, week_of)
  WHERE status IN ('running', 'shipped');
