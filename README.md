# Yoru

Audit trail for autonomous AI coding agents. Install a Claude Code hook, get a dashboard showing every tool call, file edit, and red-flag event your overnight agent ran — plus a per-session letter grade on Throughput, Reliability, and Safety.

> **Don't want to host?** Yoru Cloud is free forever for one developer at [yoru.sh](https://yoru.sh). This repo is the AGPL-licensed server for self-hosting.

## Self-host, quick path

```bash
git clone https://github.com/yoru-sh/yoru.git && cd yoru
cp backend/.env.example backend/.env   # fill in Supabase + SMTP
docker compose up -d
```

Then point the CLI at your instance:

```bash
pip install yoru-cli
yoru init --server https://yoru.acme.com
```

Full walkthrough with Supabase schema bootstrap, GitHub OAuth, and SMTP in [`docs/SELF-HOST.md`](docs/SELF-HOST.md).

## Layout

| Directory | What it is |
|---|---|
| `backend/` | FastAPI service — event ingest, red-flag detection, session scoring, Supabase-backed multi-tenant |
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

## Who builds this

yoru is built and run by the team behind **[tsukumo](https://tsukumo.ch/?utm_source=yoru&utm_medium=oss-suite&utm_campaign=consulting&utm_content=readme)** — we help engineering teams run AI coding agents in production. yoru is how we see what our own agents did; free and open source (AGPL).

Pairs with **wrai.th** (orchestration) and **trovex** (context, fewer tokens).

> Running agents in production and flying blind? That's our work — [talk to us](https://tsukumo.ch/?utm_source=yoru&utm_medium=oss-suite&utm_campaign=consulting&utm_content=readme).

## License

AGPL-3.0 · [LICENSE](./LICENSE). Modifying the server and exposing it to other users triggers the source-distribution clause — fine for internal company self-hosting, talk to us first before running a competing hosted service on top of this code.

The CLI is MIT (separate repo). See [`LICENSING.md`](./LICENSING.md) for the full rationale.

Issues and PRs welcome.

---

Running agents in production across a team? [tsukumo](https://tsukumo.ch/?utm_source=yoru&utm_medium=oss-suite&utm_campaign=consulting&utm_content=footer) consults on doing it well.
