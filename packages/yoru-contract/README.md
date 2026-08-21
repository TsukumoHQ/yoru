# yoru-contract

The co-located wire contract shared by the yoru backend and the yoru CLI —
`CanonicalEvent` (the agent-neutral event schema) and the device-code pairing
request/response shapes. Versioned in one place so the two consumers can
never drift silently.

MIT-licensed (see `LICENSE`) so it can be safely imported by the MIT
`yoru-cli` once the CLI actually consumes it — an AGPL-adjacent contract
would poison every `pip install yoru-cli`.

This package holds **schema only**: no business logic, no red-flag
detection, no database models. Consumed by path
(`[tool.uv.sources] yoru-contract = { path = "../packages/yoru-contract" }`)
from `backend/pyproject.toml` (a hard dependency — the backend is never
pip-installed externally) and from `yoru-cli/pyproject.toml`'s `dev` extra
only (the CLI doesn't import it yet, and it isn't vendored into or
published alongside the CLI wheel — a hard runtime dependency there would
break `pip install yoru-cli`). Not published to PyPI independently.

## Contents

- `yoru_contract.CanonicalEvent` — the agent-neutral event envelope
  (`schema_version`, `actor`, `agent_kind`, `action`, `tool`, `artifact`,
  `diff`, ...). A backend ahead of an old CLI's contract version branches on
  `schema_version` instead of hard-failing; old CLIs just leave new fields
  empty.
- `yoru_contract.pairing` — device-code pairing wire types
  (`DeviceCodeStartRequest`/`Response`, `DeviceCodeApproveRequest`,
  `DeviceCodePollRequest`/`Response`).
