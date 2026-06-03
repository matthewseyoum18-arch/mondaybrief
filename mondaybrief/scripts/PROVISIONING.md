# Provisioning Checklist

Everything `bootstrap.ps1` / `bootstrap.sh` cannot do for you. Work top-to-bottom.

---

## 1. External service signups (manual, 30-45 min)

Each line is a service signup that requires a human + a credit card / email
verification. None of this is scriptable.

- [ ] **Neon Postgres** — https://console.neon.tech — create Launch project, copy
      `DATABASE_URL` into `.env`. Free → $19/mo.
- [ ] **Anthropic** — https://console.anthropic.com — create API key. Set
      `ANTHROPIC_API_KEY`. Pay-as-you-go; Haiku 4.5 is the default.
- [ ] **Geocodio** — https://www.geocod.io — copy API key into `GEOCODIO_API_KEY`.
      Free tier 2,500/day is enough for one cleaner.
- [ ] **Mapbox** — https://account.mapbox.com — `MAPBOX_API_KEY`. Free 100k
      Matrix calls/mo.
- [ ] **Twilio Lookup** — https://console.twilio.com — `TWILIO_ACCOUNT_SID` +
      `TWILIO_AUTH_TOKEN`. Pay-per-lookup (~$0.008).
- [ ] **Resend** — https://resend.com — verify a sending domain, create an API
      key (`RESEND_API_KEY`), set `RESEND_FROM_EMAIL` to a mailbox on that
      domain. Add a webhook (events `email.bounced`, `email.complained`,
      `email.delivered`, ...) pointing at
      `https://<your-domain>/webhooks/resend`; copy its Svix signing secret to
      `RESEND_WEBHOOK_SECRET`.
- [ ] **Stripe** — https://dashboard.stripe.com — create $149/mo recurring
      Product + Price (`STRIPE_PRICE_ID_MONTHLY`). Copy live + test
      `STRIPE_SECRET_KEY`. Add a webhook endpoint pointing at
      `https://<your-domain>/webhooks/stripe`, copy `STRIPE_WEBHOOK_SECRET`.
- [ ] **Inngest** — https://app.inngest.com — create app `mondaybrief`. Copy
      `INNGEST_EVENT_KEY` + `INNGEST_SIGNING_KEY`. Connect to deployed URL
      `/api/inngest`.
- [ ] **Langfuse (optional)** — https://cloud.langfuse.com — project keys go
      into `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY`. Falls back to no-op
      when unset.

## 2. Domain + DNS (manual, ~1 hour + DNS propagation)

Without DNS, Resend deliverability tanks.

- [ ] Own a sender domain (e.g. `mondaybrief.app`). Set `RESEND_FROM_EMAIL`
      to a mailbox on that domain.
- [ ] Add the SPF + DKIM (and MX for Return-Path) records Resend shows in the
      Domains page to your registrar's DNS.
- [ ] Add a DMARC record (`v=DMARC1; p=quarantine; rua=mailto:dmarc@your-domain`).
- [ ] Verify the domain in the Resend dashboard before sending.

## 3. Resend deliverability warmup (manual, ~5 business days)

Fresh domains land in spam if you start at full volume.

- [ ] Send transactional only for week 1 (one onboarding email per day,
      magic-link sign-ins, dunning if any).
- [ ] Ramp the weekly brief volume gradually over week 2.
- [ ] Monitor bounce rate in the Resend dashboard; keep <2%.
- [ ] Don't switch to live outreach blast until day 7+ (and use a SEPARATE
      sender/domain for cold outreach — keep it off the transactional domain).

## 4. Schema apply (scripted)

```bash
psql "$DATABASE_URL" -f mondaybrief/schema.sql
```

Already wrapped by `bootstrap.ps1` / `bootstrap.sh` when `DATABASE_URL` is set.

## 5. Local secrets (scripted)

`bootstrap.*` calls `python scripts/gen_secrets.py` automatically. Fills the
two random secrets:

- `MAGIC_LINK_SECRET`
- `FEEDBACK_TOKEN_SECRET`

(`RESEND_WEBHOOK_SECRET` is NOT generated — it's the Svix signing secret from
the Resend dashboard webhook config; paste it into `.env` from there.)

Idempotent — re-runs only fill empty values.

## 6. Pilot client seed (scripted)

```bash
python scripts/seed_clients.py --slug spotless --email <real-spotless-email>
python scripts/seed_clients.py --slug pritchard --email <real-pritchard-email>
```

Default emails are placeholders. Override before the first live send.

## 7. Pilot customer book (NOT scriptable — sales-blocked)

`fixtures/spotless_customers.csv` and `fixtures/pritchard_customers.csv` are
research-built placeholders. They keep offline smoke tests green but the real
brief needs the cleaner's actual customer list.

**To unblock**: cold-call Spotless / Pritchard, send the demo PDF built from
public Chicago Socrata data, ask for a 47-row sample CSV in trade. Streamlit
upload page (`/`) handles ingestion.

## 8. Deploy targets

Two long-running processes + one dashboard:

- [ ] **`mondaybrief.app:app`** — public HTTPS surface. Stripe webhooks +
      checkout, Postmark webhooks, feedback links, unsubscribe. Deploy to
      Fly.io / Railway / Render.
      `uvicorn mondaybrief.app:app --host 0.0.0.0 --port $PORT`
      (repo-root `main:app` is a thin shim re-exporting this for older configs).
- [ ] **`mondaybrief.inngest.server:app`** — cron + per-client pipeline. Same
      hosting target. Inngest discovers the `/api/inngest` route automatically
      once `INNGEST_SIGNING_KEY` is set.
- [ ] **`streamlit run src/mondaybrief/ui/streamlit_app.py`** — owner dashboard.
      Streamlit Cloud (free for 1 app) or same Fly host.
- [ ] **GTK runtime on the pipeline image** — WeasyPrint needs native GTK/Pango
      libs to render the PDF. Debian/Ubuntu base image:
      `apt-get install -y libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0
      libffi-dev libcairo2`. Without it the brief render raises
      `OSError: cannot load library 'libgobject-2.0-0'`. (Local Windows dev:
      `winget install --id tschoonj.GTKForWindows -e`, then restart the shell.)

## 9. First real Monday brief

- [ ] Set `COMPANY_POSTAL_ADDRESS` (+ `COMPANY_NAME`) to a real physical
      address — CAN-SPAM requires it in the brief footer; the default is a
      placeholder that must not ship.
- [ ] Confirm `clients` row for pilot exists with real `contact_email`.
- [ ] Confirm Stripe subscription is `active` OR `clients.active = true`
      (grace-mode for unbilled pilots).
- [ ] Confirm `customers` rows exist for the pilot (via Streamlit upload).
- [ ] Wait for Monday 7am America/Chicago. Inngest cron fires.
- [ ] Inbox check + Langfuse trace + `pipeline_runs` row with non-null
      `cost_usd` and `postmark_delivery_id` = green.

If anything fails: `OPERATOR_EMAIL` gets a Postmark alert with the traceback.
