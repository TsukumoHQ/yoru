# Changelog

All notable changes to yoru (the AGPL server + dashboard) are documented here.
The CLI (`yoru-cli`, MIT) is versioned and released separately.

This project follows semantic versioning.

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
