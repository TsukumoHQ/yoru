# Yoru

Audit trail for autonomous AI coding agents. Install a Claude Code hook, get a dashboard showing every tool call, file edit, and red-flag event your overnight agent ran — plus a per-session letter grade on Throughput, Reliability, and Safety.

> **Don't want to host?** Yoru Cloud is free forever for one developer at [yoru.sh](https://yoru.sh). This repo is the AGPL-licensed server for self-hosting.

## Self-host, quick path

No external services required. Auth and data both run inside the stack by
default — SQLite on disk, password auth in-process. **No Supabase account, no
cloud database, no SMTP needed to get running.**

```bash
git clone https://github.com/yoru-sh/yoru.git && cd yoru
cp backend/.env.example backend/.env    # defaults work as-is
make dev                                # api :8002 + dashboard :5173
```

Open the dashboard — on a fresh instance you land on a **first-run wizard** that
creates your admin account (and lets you point at an existing database if you
want one). Prefer the terminal? Run it headless:

```bash
make setup            # interactive: pick a DB, create the admin
```

Then point the CLI at your instance:

```bash
pip install yoru-cli
yoru init --server https://yoru.acme.com
```

### Choose your stack

Everything below is optional — the defaults are fully local.

| Concern | Default (zero-config) | Bring your own |
|---|---|---|
| **Auth** | `AUTH_PROVIDER=local` — users in your DB, scrypt + JWT | `AUTH_PROVIDER=supabase` — hosted/self-hosted GoTrue (set `SUPABASE_*`) |
| **Database** | bundled SQLite at `backend/data/receipt.db` | any Postgres — set `RECEIPT_DB_URL=postgres://…` (or paste it in the wizard) |
| **Email** | none — welcome mail skipped | SMTP via `EMAIL_PROVIDER=smtp` + `SMTP_*` |

The **first registered user becomes the admin.** For an internet-exposed
instance, set `SETUP_TOKEN=<random>` so only someone holding the token can run
the wizard. Pin `AUTH_JWT_SECRET` in production (the wizard does this for you).

Full walkthrough — Postgres, GitHub OAuth, SMTP, and the Supabase path — in
[`docs/SELF-HOST.md`](docs/SELF-HOST.md).

## Layout

| Directory | What it is |
|---|---|
| `backend/` | FastAPI service — event ingest, red-flag detection, session scoring; pluggable auth (local or Supabase) and database (SQLite or Postgres) |
| `frontend/` | React dashboard (the app a self-hoster exposes to their team) |
| `packages/receipt-ui/` | Shared component library consumed by `frontend/` |
| `docs/` | Self-host guide, architecture, hook contract |

The CLI lives in a separate MIT repo: [github.com/yoru-sh/cli](https://github.com/yoru-sh/cli) · `pip install yoru-cli`.

## Dev setup (contributors)

```bash
make install             # uv sync (Python) + npm ci (JS)
make restart-backend     # uvicorn on :8002, idempotent
curl http://localhost:8002/health/ready
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the stack layout and boundaries.

## License

AGPL-3.0 · [LICENSE](./LICENSE). Modifying the server and exposing it to other users triggers the source-distribution clause — fine for internal company self-hosting, talk to us first before running a competing hosted service on top of this code.

The CLI is MIT (separate repo). See [`LICENSING.md`](./LICENSING.md) for the full rationale.

Issues and PRs welcome.
