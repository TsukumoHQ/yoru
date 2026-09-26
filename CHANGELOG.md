# Changelog

All notable changes to yoru (the AGPL server + dashboard) are documented here.
The CLI (`yoru-cli`, MIT) is versioned and released separately.

This project follows semantic versioning.

## [Unreleased]

### Changed

- BREAKING: service tokens are principals, not users. `POST /auth/service-token`
  no longer accepts `user_email` (422); the token identity is always
  `service:<org_id>`, and existing tokens minted with a `user_email` now resolve to
  that principal too. Scopes are enforced: `events:write` for ingest, `events:read`
  for reads, 403 elsewhere. For per-developer attribution, pair each developer's
  own CLI. (task:f71a86b2)
- Red-flag rule create, update and delete require a dashboard session; hook
  tokens, service tokens and API keys get 403. (task:f71a86b2)

## [0.4.1] - 2026-09-26

Security patch for 0.4.0, from a pre-GA penetration-test pass. No API shape
changes and no CLI change; yoru-cli stays at 0.3.0.

### Security

- Outbound webhook signing secrets are now encrypted at rest (Fernet), for both
  the per-user and the org/admin webhook surfaces. The key comes from
  `WEBHOOK_SECRET_ENCRYPTION_KEY`, or is generated once and persisted at
  `backend/data/.webhook_secret_key`; back that file up, a lost key makes stored
  secrets unreadable. The org/admin webhook API no longer returns the secret on
  every read, only on create and regenerate. (23b1a3b, task:bb1f35fc)
- The session and refresh cookies now carry `Secure` when
  `ENVIRONMENT=production`. The refresh cookie was hardcoded to `Secure=false`.
  (23b1a3b, task:bb1f35fc)
- Deleting a red-flag rule (`DELETE /orgs/{org_id}/red-flag-rules/{rule_id}`)
  now requires an org owner or admin; a plain member could delete an owner's
  rule. (23b1a3b, task:bb1f35fc)
- Updating a red-flag rule (`PATCH`) now requires an org owner or admin, or the
  rule's creator; a plain member could disable someone else's rule. Creating a
  rule stays open to members. (877d7fd, task:4e5a32c4)
- `GET /auth/org/identities`: the pentest's cross-tenant finding did not
  reproduce (the endpoint already required an org admin); the existing denial
  tests were re-run and no code changed. (23b1a3b, task:bb1f35fc)
- `POST /sessions/events` now refuses events for a session owned by another
  identity; any org's hook token could previously append to and hijack another
  identity's audit trail. (96c75fd, task:dc033681)

### Changed

- `LICENSING.md` and `LICENSE` scope no longer name the retired Receipt product,
  `receipt.dev`, or `opentruth.ch`. (e37f737, task:98ec4d50)
- Dashboard tests: `OrgIdentitiesPage` and `TokensPage` vitest cases no longer
  depend on the wall clock. Test-only, no runtime change. (2f72612,
  task:dccf35db)

## [0.4.0] - 2026-09-03

Org-scoped identity and a CTO console. Every paired CLI is now a first-class
identity that belongs to an org, the dashboard leads with exceptions instead of
raw activity, and audit capture no longer depends on Claude Code's transcript
file alone: git commits and pushes are captured and reconciled directly.

Pairs with yoru-cli 0.3.0. A 0.2.x CLI still pairs and streams events; it just
does not carry identity slots, git capture, or the tailer fixes, which live in
the CLI.

### Added

- Identity keystone. The paired CLI token is the identity: approval records the
  machine hostname, org admins list identities per org (`GET
  /auth/org/identities`), and the dashboard shows both the org view and "My
  machines" with hostname and last-seen, so a device that went quiet stands
  out. (f96cace, 2434351, e53ad0d, 57c5a92)
- Default org routing. An event that matches no workspace rule falls back to
  the token's `default_org_id` workspace instead of landing in "Personal"; the
  dashboard shows an explicit header while that fallback is in effect.
  (d305f70, 352cfe8)
- Token analytics. Day/week token and cost rollups at `GET /analytics/tokens`,
  drillable by org, user, and repo. The Usage page is redesigned as an org-cost
  console for the CTO persona. (4b7021c, 69a9659, 60be1ba, 85c5775)
- Exceptions rollup. Org-wide red-flag BI: flagged sessions per period, top
  rules, top offenders. `GET /activity` gains a `flagged` filter.
  (fc05b56, 8c8718a)
- Exception-first dashboard. `/` and `/feed` lead with flagged sessions by
  default; the full activity stream is one click away. (c9d556b)
- Session detail leads with the synthesis card; the heavy panels (cost,
  integrity, causal replay, step-through replay, full timeline) collapse below
  it. (668e796)
- Skill-safety red flags. A deterministic 16-rule category (prompt injection,
  reverse shells, exfiltration, hard-coded secrets, credential-path reads,
  cloud-metadata probes) evaluated on events that touch skill content. No LLM,
  not user-editable. (04810c7)
- Custom red-flag rules show their org-defined name and severity as badges
  across the dashboard, with follow-ups on the session hero.
  (026685c, 60b6987)
- Git capture (B3). The CLI installs opt-in `post-commit`/`pre-push` hooks and
  reconciles `git log` every 60s so `--no-verify` and pre-init commits still
  land. The backend ingests commit and force-push events under a stable session
  id, and session detail shows an audit badge with `agent_confidence`
  (declared/unknown) and whether enforcement is available.
  (e050c8d, a238b6e, b62cbcd, db18309, 631ee94)
- Canonical event. Red-flag scanning runs on a provider-neutral
  `CanonicalEvent` instead of Claude Code's raw event shape, so events from
  other agents get the same rules. (e31f64a)
- Shared wire contract package `packages/yoru-contract` (MIT) for the
  device-code pairing DTOs used by both the CLI and the backend. (7bdb49d)
- Pairing page distinguishes invalid, expired, and already-paired codes.
  (283a717)
- Frontend test harness (vitest + Testing Library) and a hermetic Playwright
  screenshot suite. (852790e, 14e1e7e)
- Authz primitives documented: `require_org_admin` gates org-wide actions,
  `visible_scope_sync` decides which rows a caller sees. (3d4e97f)

### Fixed

- Org selection stays in sync across browser tabs. (082e791)
- Organizations page no longer crashes when the API returns a non-array
  `items`. (ae29126, 18c1fb1)
- Marketing hero no longer overflows on mobile. (6e6b20a)

### CI

- `skill-gate` review-verdict check uses a general left-anchored regex with a
  pinned selftest, so domain-scoped review skills pass. (1140bef)

### CLI (yoru-cli 0.3.0, released alongside)

The CLI is versioned separately; these landed in this repo over the same
window and ship as `yoru-cli` 0.3.0.

- Identity slots. Local state (`config.json`, `tail-state.json`,
  `enforce.json`) is scoped per paired identity instead of per `$HOME`, so two
  devs on one machine, or one dev re-pairing, no longer overwrite each other.
  (73c5f3f)
- `yoru doctor` prints the active identity and warns when several slots exist.
  (813e2ab)
- `yoru logout` and token rotation without a full re-pair. (9cb68fd)
- `yoru init --force` revokes the previous token server-side instead of only
  overwriting the local file. (527d6d9)
- Git capture: `yoru init` offers opt-in `post-commit`/`pre-push` hooks for
  that repo, plus a 60s `git log` reconcile that backfills commits made with
  `--no-verify` or before init; hook and reconcile paths produce the same
  session id and entry uuid. Hostname is sent at pairing.
  (e050c8d, a238b6e, b62cbcd, f1fbf9d, f96cace)
- Force-push events carry their kind. (db18309)
- Transcript tailer: `tail-state.json` guarded by `flock` (run lock + state
  lock) so two tailers cannot clobber each other (832c4f9); `yoru status` no
  longer truncates the run-lock file while probing it (d7e6f2f); drains stream
  the unread remainder instead of one whole-file read (bfa86a0); backfill no
  longer races the running tailer's in-memory state (307778e).

## [0.2.0] - 2026-06-27

Local-export sharing. Turn any session into a shareable image rendered on your
own instance. Nothing leaves your self-hosted box except the image you choose
to share. There is no hosted viewer and no public URL.

### Added

- Shareable receipt PNG. New authed endpoint `GET /sessions/{id}/receipt.png`
  renders a self-contained card (grade, Throughput / Reliability / Safety
  subscores, tool / file / flag counts, title) server-side with Pillow.
  Download or copy it from the dashboard session view. (#122)
- Shareable replay GIF. New authed endpoint `GET /sessions/{id}/replay.gif`
  renders an animated step-through of the session, one frame per event so idle
  gaps collapse, ending on the grade frame. Download it from the dashboard.
  (#124)
- Live replay player in the dashboard. Step through a session with a scrubber,
  play / pause, keyboard controls, and red-flag jump markers. (#125)

### Changed

- Durable event ingestion. Tool calls, prompts, and notifications now come from
  the durable transcript tailer instead of the synchronous hook, with a stable
  per-event dedup key, so events survive backend downtime without
  double-counting. (#121)
- The dashboard share action is now local image export (Download PNG, Copy
  image, Download GIF). The opt-in public-session path stays private by default
  and is no longer surfaced in the dashboard.

### Security

- Every exported image runs the redaction pass before render: secrets, absolute
  home paths, and git-remote identity are scrubbed, and the export endpoints are
  owner-only (cross-user or unknown ids return 404, so they cannot be used to
  probe other users' sessions).

## [0.1.0] - 2026-06-26

Initial public release. Claude Code hook captures every tool call, prompt, and
red-flag event; the dashboard shows a timeline, an A to F grade across
Throughput / Reliability / Safety, a tamper-evident audit trail, and a
single-file JSON export. Self-host only: SQLite plus local auth by default,
Postgres / Supabase / OAuth / SMTP all optional.

[0.2.0]: https://github.com/TsukumoHQ/yoru/releases/tag/v0.2.0
[0.1.0]: https://github.com/TsukumoHQ/yoru/releases/tag/v0.1.0
