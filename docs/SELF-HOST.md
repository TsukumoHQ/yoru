# Self-hosting Yoru

**Yoru is self-hosted.** You run the backend + dashboard on your own box; your
session data never leaves your network. This repo is the AGPL-licensed server —
there is no hosted Yoru to sign up for. Everything below is about standing up
*your* instance.

The default stack is **fully local**: a bundled SQLite database on disk and
in-process password auth. No Supabase, no cloud database, no SMTP, no external
service of any kind is required to get running. Postgres, Supabase auth, SMTP,
and GitHub OAuth are all **optional "bring your own" upgrades** you can layer on
when you want them.

> **Scope**: this guide covers the single-company self-host path — one backend
> box, one static frontend, your domain. Local / bare-metal is the *default*,
> not a future phase.

---

## Quick local path (zero external services)

The fastest way to a running instance — no accounts, no provisioning:

```bash
git clone https://github.com/TsukumoHQ/yoru.git && cd yoru
cp backend/.env.example backend/.env    # defaults work as-is
make dev                                # api :8002 + dashboard :5173
```

Open the dashboard. On a fresh instance you land on a **first-run wizard** that
creates your admin account (and optionally lets you point at an existing
database). **The first registered user becomes the admin.**

Prefer the terminal? Run the wizard headless — handy for CI / SSH installs with
no browser:

```bash
make setup            # interactive: pick a DB, create the admin
```

That's it. With the defaults you now have:

- **Database**: bundled SQLite at `backend/data/receipt.db` (the Docker image
  uses `/app/data/receipt.db` — persist that volume).
- **Auth**: `AUTH_PROVIDER=local` — users live in the app DB, passwords hashed
  with scrypt, sessions signed with a JWT. No external identity provider.
- **Billing**: off. `BILLING_ENABLED=false` (the default) means unlimited
  ingest, no paywall, no Stripe.
- **Email**: off. With no `SMTP_*` configured, invitations run in-app and email
  sends become no-ops — the instance boots fine without SMTP.

Then point the CLI at your instance (see [CLI pairing](#5-configure-the-cli-to-talk-to-your-backend)):

```bash
pip install yoru-cli
yoru init --server https://yoru.acme.com
```

### Choose your stack

Everything here is optional — the defaults are fully local.

| Concern | Default (zero-config) | Bring your own |
| --- | --- | --- |
| **Database** | bundled SQLite (`RECEIPT_DB_URL=sqlite:///…`) | any Postgres — `RECEIPT_DB_URL=postgres://…` (or paste it in the wizard) |
| **Auth** | `AUTH_PROVIDER=local` — users in your DB, scrypt + JWT | `AUTH_PROVIDER=supabase` — hosted/self-hosted GoTrue (set `SUPABASE_*`) |
| **Email** | none — welcome/invite mail skipped | SMTP via `EMAIL_PROVIDER=smtp` + `SMTP_*` |
| **Social sign-in** | n/a | GitHub OAuth (configured in your Supabase project) |
| **Billing** | off — everything unlocked | n/a — self-host has no paywall |

For an internet-exposed instance, set `SETUP_TOKEN=<random>` so only someone
holding the token can run the wizard, and pin `AUTH_JWT_SECRET` (the wizard does
this for you; otherwise it auto-generates a stable secret at
`backend/data/.auth_jwt_secret` on first boot).

---

## Environment variables

The backend is a single FastAPI app in `backend/`. The authoritative list lives
in `.env.example`; the core knobs:

```bash
# === Core ===
# App DB for sessions/events/hook-tokens AND (with local auth) users.
# SQLite by default — Docker path uses four slashes (absolute /app/data/…).
RECEIPT_DB_URL=sqlite:////app/data/receipt.db
RECEIPT_VERSION=0.1.0
ENV=production

# === Auth provider ===
# 'local'   = self-contained dashboard auth (scrypt + JWT). No external service.
# 'supabase'= delegate identity to a Supabase GoTrue project (fill the block below).
AUTH_PROVIDER=local

# Signing secret for local access tokens. Leave unset to auto-generate a stable
# secret at backend/data/.auth_jwt_secret on first boot. SET EXPLICITLY in prod.
# AUTH_JWT_SECRET=

# === Billing ===
# OFF by default for self-host: ingest is unlimited, no Stripe/paywall surfaces.
BILLING_ENABLED=false

# === Domain / CORS ===
RECEIPT_DOMAIN=yoru.acme.com
CORS_ALLOWED_ORIGINS=https://yoru.acme.com

# === First-run wizard hardening (recommended when internet-exposed) ===
# SETUP_TOKEN=<openssl rand -hex 24>

# === Supabase (auth) — only when AUTH_PROVIDER=supabase ===
# SUPABASE_URL=https://<your-project>.supabase.co
# SUPABASE_ANON_KEY=<anon key>
# SUPABASE_JWT_SECRET=<jwt secret>

# === Email (SMTP) — only if you want welcome / invite mails ===
# EMAIL_PROVIDER=smtp
# EMAIL_BRAND_NAME=Yoru
# EMAIL_SUPPORT_EMAIL=yoru@acme.com
# EMAIL_COMPANY_ADDRESS="Acme, Paris"
# SMTP_HOST=smtp.resend.com
# SMTP_PORT=465
# SMTP_USERNAME=resend
# SMTP_PASSWORD=<re_... token>
# SMTP_FROM_EMAIL=yoru@acme.com
# SMTP_FROM_NAME=Yoru
# SMTP_USE_TLS=true
```

---

## Bring your own (optional production upgrades)

Reach for these only when the local defaults aren't enough. Each is independent
— you can adopt Postgres without touching auth, add SMTP without touching the
database, and so on.

### Postgres instead of SQLite

SQLite is fine for a single box. For multiple replicas, point-in-time backups,
or higher write volume, swap in any Postgres by setting one variable:

```bash
RECEIPT_DB_URL=postgres://user:pwd@db.internal:5432/yoru
```

You can also paste the connection URL into the first-run wizard (web or
`make setup`); it tests the connection before writing it. Switching the DB after
setup requires a backend restart (`make down && make dev`). The schema is
applied automatically on boot — no manual migration step.

### Supabase auth instead of local auth

If your team already authenticates against Supabase (or you want hosted/
self-hosted GoTrue magic-links and social sign-in), set:

```bash
AUTH_PROVIDER=supabase
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_ANON_KEY=<anon key>
SUPABASE_JWT_SECRET=<jwt secret>
```

With `AUTH_PROVIDER=supabase`, the local first-run wizard is bypassed —
identity is managed entirely in your Supabase project. Create users / configure
providers in the Supabase dashboard rather than the Yoru wizard.

### GitHub OAuth (optional, via Supabase)

Social sign-in rides on the Supabase auth path, so it's only relevant when
`AUTH_PROVIDER=supabase`:

1. Create a GitHub OAuth App at https://github.com/settings/applications/new
   - Homepage URL: `https://yoru.acme.com`
   - Authorization callback URL: **your Supabase project's** GitHub callback,
     shown under Auth → Providers → GitHub.
2. In the Supabase dashboard → Auth → Providers → **GitHub** → paste the
   `client_id` + `client_secret` → toggle Enabled.
3. Auth → URL Configuration:
   - Site URL: `https://yoru.acme.com`
   - Redirect URLs: your dashboard origin (plus `http://localhost:5173` for
     local dev).

### SMTP (optional, welcome / invite / alert mail)

Without SMTP the instance runs fine and email sends are no-ops. To turn on
transactional mail, set `EMAIL_PROVIDER=smtp` plus the `SMTP_*` block above
(`SMTP_HOST` / `SMTP_USERNAME` / `SMTP_PASSWORD` must all be present for the
provider to activate). Resend, Postmark, Mailgun, or your corporate SMTP all
work. The `sendgrid` and `resend` providers are also supported via
`SENDGRID_API_KEY` / `RESEND_API_KEY` respectively.

### Billing — there is no paywall

Self-host has **no billing**. `BILLING_ENABLED=false` (the default) means
unlimited ingest and no plan gating; self-hosters effectively run with
everything unlocked. Independently, if billing surfaces are ever toggled on, an
empty `STRIPE_API_KEY` keeps the backend in **mock mode**:

- the dashboard hides the "Upgrade" / "Manage subscription" buttons, and
- `/billing/checkout-session` and `/billing/portal-session` return a graceful
  mock URL instead of erroring.

You never need a Stripe account to self-host.

---

## Deploy with Docker

```bash
cd backend
docker build -t yoru-backend:latest .
docker run -d \
  --name yoru-backend \
  --env-file .env \
  -p 8002:8002 \
  -v /srv/yoru-data:/data \
  --restart unless-stopped \
  yoru-backend:latest
```

The backend exposes `:8002` — put any TLS-terminating proxy in front of it
(Caddy / nginx / Traefik). The `/data` volume holds the SQLite DB where session
events (and, with local auth, your users) are written; back it up with your
normal snapshot rotation.

Health check: `curl https://yoru.acme.com/health` → `200`.

---

## Deploy the frontend + marketing

Both are Vite apps. Build once, serve the `dist/` folder from anywhere.

```bash
# Dashboard
cd frontend
cp .env.example .env.production
# edit .env.production:
#   VITE_API_URL=https://yoru.acme.com/api/v1
#   (only if using Supabase auth:)
#   VITE_SUPABASE_URL=https://<your-project>.supabase.co
#   VITE_SUPABASE_ANON_KEY=<anon>
npm install
npm run build
# serve dist/ from nginx / Cloudflare Pages / Vercel / …

# Marketing (optional — skip if you don't want a public landing page)
cd ../marketing
npm install
npm run build
```

If you use nginx, mount `dist/` as the web root and add SPA fallback so deep
links resolve:

```nginx
location / {
    try_files $uri /index.html;
}
```

---

## 5. Configure the CLI to talk to your backend

Users of your instance install the public CLI from PyPI and point it at your
server. **`--server` is required** — there is no default server to fall back to:

```bash
pip install yoru-cli
yoru init --server https://yoru.acme.com
```

The browser opens your dashboard's pairing page for approval. From there the CLI
writes `~/.config/yoru/config.json` with your server URL and a paired token.
Everything else — Claude Code hooks, event ingest, dashboard polling — works
against your instance.

If you run an air-gapped environment where PyPI isn't reachable, mirror the
wheel from `pip download yoru-cli` into your internal index and install from
there (still passing `--server`).

---

## Verification checklist

Run through these after the first deploy; they confirm the full stack:

- [ ] `curl https://yoru.acme.com/health` returns `200`
- [ ] The first-run wizard creates your admin account (or `make setup` does)
- [ ] Sign in to the dashboard with that admin account
- [ ] `yoru init --server https://yoru.acme.com` pairs cleanly
- [ ] Run `claude` in any git repo → a tool call appears in the dashboard
      within ~5 s
- [ ] Drop a known-secret pattern in a Bash tool call (e.g.
      `echo AKIAIOSFODNN7EXAMPLE`) → the session shows a `[secret]` red flag
- [ ] No "Upgrade" buttons appear (billing is off for self-host)
- [ ] (If SMTP configured) a welcome / invite email lands in the inbox

All green → you're live.

---

## Upgrading

Pin a Docker tag and upgrade on your own cadence:

```bash
# Pull the new image, restart, let the app auto-apply schema migrations on boot
docker pull ghcr.io/helios-code/yoru-backend:latest
docker stop yoru-backend && docker rm yoru-backend
docker run -d --name yoru-backend --env-file .env -p 8002:8002 \
  -v /srv/yoru-data:/data --restart unless-stopped \
  ghcr.io/helios-code/yoru-backend:latest
```

Schema changes are applied automatically at startup for both SQLite and
Postgres. Watch the release notes — breaking changes are tagged with a 🚨.

---

## Security notes

- **AGPL-3.0**: modifying the backend and exposing it to *other organizations*
  triggers the AGPL's distribution clause — you must offer source to those
  users. Internal single-org use is unrestricted. (The CLI is MIT and carries
  no such obligation.)
- **`SETUP_TOKEN`**: any internet-exposed instance should set this before first
  boot so a stranger can't reach the open first-run wizard and claim the admin
  account.
- **`AUTH_JWT_SECRET`**: pin it explicitly in production. If unset it
  auto-generates to `backend/data/.auth_jwt_secret` — fine for a single box,
  but set it yourself when running multiple replicas so tokens stay valid
  across them.
- **Secrets hygiene**: `AUTH_JWT_SECRET`, `SUPABASE_JWT_SECRET`, and
  `SMTP_PASSWORD` never need to reach any client. Keep them on the backend
  process only.
- **Supabase RLS** (only if `AUTH_PROVIDER=supabase`): the backend mediates all
  writes; a leaked anon key still only exposes what a user's own row grants.

---

## When to reach out

Open an issue on `github.com/TsukumoHQ/yoru` for:

- A migration that fails on your database (we'll ship a fix)
- A feature you expected to be available that isn't
- Anything in this guide that didn't match what you saw

Or email `hello@yoru.sh` for general contact.
