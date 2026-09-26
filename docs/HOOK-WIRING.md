# Hook Wiring

How Receipt attaches to Claude Code, Codex, OpenCode, and Cursor sessions, mints a token, and starts streaming events. Read this when the README Quickstart isn't enough, or before you commit to `receipt init`.

**Status:** `v0 / MVP` · **Backend:** `http://localhost:8002` by default

---

## 1. Why a hook, not a wrapper?

Claude Code and Codex both expose first-class hook APIs: events hand JSON blobs to user-configured scripts, which run out-of-band and cannot block the agent. Receipt registers hooks. It does **not** wrap, proxy, or fork the agent binaries — your session keeps running untouched whether Receipt is up, down, or missing entirely. The hooks are passive observers; if they fail, the agents don't notice.

## 2. Install

```bash
# Editable install from the monorepo (v0)
pip install -e receipt-cli/

# Once published on PyPI:
# pip install receipt-cli
```

Requires Python 3.10+. The only runtime dep is `httpx`. Backend must be reachable — default `http://localhost:8002`, override with `--server`.

Verify:

```bash
receipt --version           # → receipt 0.1.0
curl http://localhost:8002/health/ready    # → {"status":"ok", ...}
```

## 3. Mint a hook-token

A hook-token is an opaque `rcpt_...` string scoped to one username. It's minted once and reused for every event.

### 3a. Automatic — `receipt init`

```bash
receipt init --user you@example.com --server http://localhost:8002
```

This does three things in one step:

1. Mints a token (`POST /api/v1/auth/hook-token`).
2. Writes `~/.config/receipt/config.json` (mode `0600`) with the token + server URL.
3. Writes `~/.claude/hooks/receipt.sh` — the bash script Claude Code will invoke.

Re-run with `--force` to overwrite an existing install. Exit codes: `0` ok, `1` already installed, `2` auth/mint failed, `3` 4xx, `4` 5xx/network.

### 3b. Manual — for air-gapped, CI, or shared hosts

Mint the token with curl, then drop it into the config file yourself:

```bash
# 1. Mint
curl -sS -X POST http://localhost:8002/api/v1/auth/hook-token \
  -H 'Content-Type: application/json' \
  -d '{"user":"you@example.com"}'
# → {"token":"rcpt_...", "user_id":"...", "user":"you@example.com"}

# 2. Save it (same shape receipt init writes)
mkdir -p ~/.config/receipt && chmod 700 ~/.config/receipt
cat > ~/.config/receipt/config.json <<EOF
{"server":"http://localhost:8002","token":"rcpt_..."}
EOF
chmod 600 ~/.config/receipt/config.json

# 3. Install the bundled hook script
receipt init --token rcpt_... --server http://localhost:8002 --force
```

Mint is **unauthenticated in v0** (Supabase JWT gating lands in v1). A 422 at this step means the body is missing `user`; check your `-d` payload.

## 4. Wire the hook into Claude Code

`receipt init` drops the script at `~/.claude/hooks/receipt.sh` but does **not** edit your Claude Code settings — you register the hook yourself. Add this to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {"hooks": [{"type": "command", "command": "~/.claude/hooks/receipt.sh"}]}
    ]
  }
}
```

Notes:

- Use `PostToolUse`, not `PreToolUse` — `PostToolUse` carries `tool_response`, which Receipt needs to classify errors.
- The outer array is a list of matcher groups (one per tool filter); the inner `hooks` array is the list of commands that fire for that group. Match-all is the empty/absent matcher.
- If you already have other `PostToolUse` hooks, append a new object to the outer array rather than nesting.

Confirm Claude Code picked it up:

```bash
ls -la ~/.claude/hooks/receipt.sh        # must be executable (0755)
jq '.hooks.PostToolUse' ~/.claude/settings.json   # must show the receipt.sh entry
```

## 4b. Wire the hook into Codex

Codex hooks are enabled by default unless `[features].hooks=false` in your Codex config. Hooks can be configured globally at `~/.codex/hooks.json` or per-repo at `.codex/hooks.json`. Command hooks must be reviewed and trusted via `/hooks` before Codex will execute them.

The hook script needs to be installed and registered:

```bash
# 1. Copy the Codex hook script to your hooks directory
mkdir -p ~/.codex/hooks
cp backend/apps/api/api/routers/receipt/hooks/codex_hook.py ~/.codex/hooks/yoru.py
chmod +x ~/.codex/hooks/yoru.py

# 2. Add the hook configuration to ~/.codex/hooks.json
# (Create the file if it doesn't exist)
cat > ~/.codex/hooks.json <<EOF
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/hooks/yoru.py"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/hooks/yoru.py"
          }
        ]
      },
      {
        "matcher": "apply_patch",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/hooks/yoru.py"
          }
        ]
      },
      {
        "matcher": "Edit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/hooks/yoru.py"
          }
        ]
      },
      {
        "matcher": "Write",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/hooks/yoru.py"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/hooks/yoru.py"
          }
        ]
      },
      {
        "matcher": "apply_patch",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/hooks/yoru.py"
          }
        ]
      },
      {
        "matcher": "Edit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/hooks/yoru.py"
          }
        ]
      },
      {
        "matcher": "Write",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/hooks/yoru.py"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/hooks/yoru.py"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.codex/hooks/yoru.py"
          }
        ]
      }
    ]
  }
}
EOF

# 3. Trust the hook script via Codex interactive CLI
# Run Codex and use the /hooks command to review and trust the hook
codex
# In the Codex CLI, run: /hooks
# Follow the prompts to review and trust ~/.codex/hooks/yoru.py
```

Notes:

- Codex hooks use Python scripts instead of bash scripts
- The hook events are similar to Claude Code but with some differences in event names and payloads
- The hook script reads JSON from stdin and transforms Codex events to Yoru's format
- Hook input includes: `session_id`, `cwd`, `hook_event_name`, `tool_name`, `tool_input`, `tool_response` for relevant events
- The script includes git context extraction for workspace routing
- Matchers support: `Bash`, `apply_patch`, `Edit`, `Write`, and MCP tool names (e.g., `mcp__fs__read`)
- PreToolUse and PostToolUse support the same matchers for comprehensive tool coverage
- If you use MCP tools, add their names as additional matchers to capture those events
- If you already have other Codex hooks, merge the Yoru hooks into your existing configuration
- **Command hooks must be reviewed and trusted via the interactive `/hooks` command in Codex CLI**
- For vetted one-off automation, use `--dangerously-bypass-hook-trust` flag (not recommended for production)

Confirm Codex picked it up:

```bash
ls -la ~/.codex/hooks/yoru.py           # must be executable
jq '.hooks' ~/.codex/hooks.json         # must show the yoru.py entries
# Run Codex and use /hooks to verify the hook is trusted
```

## 4c. Wire the plugin into OpenCode

OpenCode uses TypeScript plugins defined in `.opencode/plugins/` (repo-local) or `~/.config/opencode/plugins/` (global). Plugins are auto-loaded by OpenCode. External dependencies go in `.opencode/package.json`, and OpenCode runs `bun install` at startup.

**Note on event sources:** This implementation uses OpenCode's plugin event system (session.created, tool.execute.after, file.edited, etc.) rather than the web/SSE streaming endpoint (`GET /global/event`). The plugin approach was chosen because:
- It runs in-process with OpenCode, avoiding network overhead and SSE connection management
- It provides structured event objects with typed properties
- It's the recommended integration pattern for OpenCode extensions
- The SSE endpoint is better suited for external monitoring systems that don't run inside OpenCode

If you need the SSE streaming approach (e.g., for an external service), OpenCode exposes `GET /global/event` as an SSE stream for global events. This is out of scope for this plugin but can be implemented separately.

```bash
# 1. Create the plugins directory if it doesn't exist
mkdir -p .opencode/plugins

# 2. Copy the Yoru plugin to your plugins directory
cp backend/apps/api/api/routers/receipt/hooks/opencode-plugin.ts .opencode/plugins/yoru.ts

# 3. Add dependencies to .opencode/package.json (create if needed)
cat > .opencode/package.json <<EOF
{
  "dependencies": {
    "@opencode-ai/plugin": "latest"
  }
}
EOF

# 4. OpenCode will automatically run bun install at startup
# No manual install step needed
```

The plugin will automatically subscribe to OpenCode events and stream them to Yoru with `agent="opencode"`.

Notes:

- OpenCode plugins are TypeScript modules that export plugin functions
- Local plugins in `.opencode/plugins/` and global plugins in `~/.config/opencode/plugins/` are auto-loaded
- External dependencies in `.opencode/package.json` are installed via `bun install` at startup
- The plugin handles key events: session.created, session.idle, tool.execute.after, file.edited
- The plugin reads Yoru config from ~/.config/yoru/config.json
- The plugin is non-blocking and won't interfere with OpenCode execution
- Plugin events are used instead of SSE streaming for in-process integration

## 4d. Wire the hook into Cursor Agent

**NOTE: Cursor Agent hook support is currently speculative.** The hook contract (event names, payload format, configuration) has not been verified against official Cursor Agent documentation. The implementation below is based on assumptions and may not work with actual Cursor Agent releases. Use at your own risk and verify against current Cursor Agent documentation.

Cursor Agent uses hooks configured in `.cursor/hooks.json`. The hook script needs to be installed and registered:

```bash
# 1. Copy the Cursor hook script to your hooks directory
mkdir -p .cursor/hooks
cp backend/apps/api/api/routers/receipt/hooks/cursor_hook.py .cursor/hooks/yoru.py
chmod +x .cursor/hooks/yoru.py

# 2. Add the hook configuration to .cursor/hooks.json
# (Create the file if it doesn't exist)
cat > .cursor/hooks.json <<EOF
{
  "hooks": {
    "sessionStart": [
      {
        "type": "command",
        "command": "python3 .cursor/hooks/yoru.py"
      }
    ],
    "postToolUse": [
      {
        "type": "command",
        "command": "python3 .cursor/hooks/yoru.py"
      }
    ],
    "afterShellExecution": [
      {
        "type": "command",
        "command": "python3 .cursor/hooks/yoru.py"
      }
    ],
    "afterFileEdit": [
      {
        "type": "command",
        "command": "python3 .cursor/hooks/yoru.py"
      }
    ],
    "sessionEnd": [
      {
        "type": "command",
        "command": "python3 .cursor/hooks/yoru.py"
      }
    ]
  }
}
EOF
```

Notes:

- Cursor hooks use Python scripts (or TypeScript) that communicate via stdio JSON
- The hook events are similar to Claude Code but with Cursor-specific naming
- The hook script reads JSON from stdin and transforms Cursor events to Yoru's format
- The script includes git context extraction for workspace routing
- If you already have other Cursor hooks, merge the Yoru hooks into your existing configuration

Confirm Cursor picked it up:

```bash
ls -la .cursor/hooks/yoru.py            # must be executable
jq '.hooks' .cursor/hooks.json          # must show the yoru.py entries
```

## 5. Verify the first event arrives

### For Claude Code

Three-step smoke against a live backend:

```bash
# a. Stash the token for the curl
TOKEN=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.config/yoru/config.json")))["token"])')

# b. Run any Claude Code tool (Write, Bash, Read) in a fresh session.
#    The hook fires on each PostToolUse and POSTs to /api/v1/sessions/events.

# c. List your sessions — the new one should show up within 5 seconds.
curl -sS -H "Authorization: Bearer $TOKEN" http://localhost:8002/api/v1/sessions | jq '.items[0]'
```

Expected shape on `items[0]`:

```json
{"id":"<session-uuid>","user":"you@example.com","agent":"claude-code",
 "tools_count":1,"files_count":0,"flagged":false,"flags":[], ...}
```

No item? See §6. Existence-but-empty (0 events, no tools counted)? Also §6 — almost always a silent 4xx.

### For Codex

Similar verification process:

```bash
# a. Stash the token for the curl
TOKEN=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.config/yoru/config.json")))["token"])')

# b. Run any Codex command (Bash, file edit) in a fresh session.
#    The hook fires on SessionStart, PostToolUse, UserPromptSubmit, and Stop.

# c. List your sessions — the new one should show up within 5 seconds with agent="codex"
curl -sS -H "Authorization: Bearer $TOKEN" http://localhost:8002/api/v1/sessions | jq '.items[0]'
```

Expected shape on `items[0]` for Codex:

```json
{"id":"<session-uuid>","user":"you@example.com","agent":"codex",
 "tools_count":1,"files_count":0,"flagged":false,"flags":[], ...}
```

### For OpenCode

Similar verification process:

```bash
# a. Stash the token for the curl
TOKEN=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.config/yoru/config.json")))["token"])')

# b. Run any OpenCode command in a fresh session.
#    The plugin will automatically stream events to Yoru.

# c. List your sessions — the new one should show up within 5 seconds with agent="opencode"
curl -sS -H "Authorization: Bearer $TOKEN" http://localhost:8002/api/v1/sessions | jq '.items[0]'
```

Expected shape on `items[0]` for OpenCode:

```json
{"id":"<session-uuid>","user":"you@example.com","agent":"opencode",
 "tools_count":1,"files_count":0,"flagged":false,"flags":[], ...}
```

### For Cursor Agent

Similar verification process:

```bash
# a. Stash the token for the curl
TOKEN=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.config/yoru/config.json")))["token"])')

# b. Run any Cursor Agent command in a fresh session.
#    The hook fires on sessionStart, postToolUse, afterShellExecution, etc.

# c. List your sessions — the new one should show up within 5 seconds with agent="cursor"
curl -sS -H "Authorization: Bearer $TOKEN" http://localhost:8002/api/v1/sessions | jq '.items[0]'
```

Expected shape on `items[0]` for Cursor:

```json
{"id":"<session-uuid>","user":"you@example.com","agent":"cursor",
 "tools_count":1,"files_count":0,"flagged":false,"flags":[], ...}
```

### Cursor-specific issues

**IMPORTANT: Cursor Agent hook support is speculative and unverified.** The following issues are based on assumed behavior and may not match actual Cursor Agent implementation.

**Hook script not found**
```bash
ls -la .cursor/hooks/yoru.py  # Should exist and be executable
```

**hooks.json syntax error**
```bash
python3 -m json.tool .cursor/hooks.json  # Validate JSON syntax
```

**Hook not firing**
- Check that Cursor hooks are enabled in your Cursor configuration
- Verify the hook names match what Cursor emits
- Check Cursor logs for hook execution errors

**Git context missing**
- The hook attempts to extract git remote/branch for workspace routing
- If git commands timeout (1 second), the hook proceeds without git context
- This is non-blocking - sessions will still be recorded, just without routing

**Contract mismatch**
- The hook event names and payload format are assumed and may not match Cursor Agent's actual API
- Verify against official Cursor Agent documentation before using in production
- Test with a real Cursor Agent installation to validate the hook contract

### Codex-specific issues

**Hook script not found**
```bash
ls -la ~/.codex/hooks/yoru.py  # Should exist and be executable
```

**hooks.json syntax error**
```bash
python3 -m json.tool ~/.codex/hooks.json  # Validate JSON syntax
```

**Hook not firing**
- Check that Codex hooks are enabled in your Codex configuration
- Verify the hook event names match what Codex emits
- Check Codex logs for hook execution errors

**Git context missing**
- The hook attempts to extract git remote/branch for workspace routing
- If git commands timeout (1 second), the hook proceeds without git context
- This is non-blocking - sessions will still be recorded, just without routing

### OpenCode-specific issues

**Plugin not found**
```bash
ls -la .opencode/plugins/yoru.ts  # Should exist
```

**Plugin build errors**
- Ensure you have the required dependencies: `@opencode-ai/plugin`, `node-fetch`
- Check that your OpenCode setup supports TypeScript plugins
- Verify the plugin syntax matches OpenCode's plugin API

**Plugin not loading**
- Check OpenCode logs for plugin loading errors
- Verify the plugin exports the correct function signature
- Ensure the plugin file is in the correct directory

**Config file missing**
- The plugin reads Yoru config from ~/.config/yoru/config.json
- Ensure the config file exists with valid server and token fields

### 401 Unauthorized on `/sessions/events` or `/sessions`

Your bearer token is missing, malformed, or revoked. Check:

```bash
cat ~/.config/receipt/config.json | jq .token      # starts with rcpt_
curl -sS -X GET http://localhost:8002/api/v1/auth/hook-tokens \
  -H "Authorization: Bearer $TOKEN" | jq .         # 200 if token valid
```

Fix: re-mint via `receipt init --force --user you@example.com`. (Old token rows are soft-revoked, not deleted — the new one is what `config.json` now holds.)

### 422 Unprocessable on ingest

`EventIn` validation failed. The response envelope carries `request_id`; grep the backend log for it:

```bash
grep <request_id> /tmp/uvicorn.log    # or wherever you piped stdout
```

Most common causes in v0:

- `session_id` missing — it is the only unconditionally required field.
- Body not wrapped as `{"events":[<event>]}` — the hook always wraps, but hand-crafted curls often skip it.
- Neither `user` in body **nor** a valid `Authorization: Bearer` header — both are optional, but at least one must be present so the server can attribute the event.

### Silent fail: hook exits 0, no event ever ingested

By design, the hook ends with `curl ... >/dev/null 2>&1 || true`. It **always** exits `0` regardless of backend status — exit code is not a signal of success.

Reproduce with the real exit status visible:

```bash
# Synthetic event matching the real Claude Code stdin shape
echo '{"session_id":"smoke-manual","tool_name":"Bash","tool_input":{"command":"ls"},"hook_event_name":"PostToolUse"}' \
  | bash -x ~/.claude/hooks/receipt.sh
```

The `bash -x` trace prints every curl + Python sub-invocation. Drop `|| true` and `>/dev/null 2>&1` from a local copy of the script if you want a persistent error stream while debugging.

### Events land but `path` / `content` are null (red flags never fire)

Real Claude Code 2026 stdin delivers `tool_input.file_path` (Write/Edit/Read) and `tool_input.command` (Bash) — not the flat `path` / `content` fields that v0's `EventIn` expects. The v0 backend classifies `kind` correctly from `tool_name` but silently drops unknown top-level keys, so `path` and `content` persist as `null`. Red-flag rules that scan `content` (`shell_rm`, `secret_aws`, `env_mutation`) can't fire. This is a known v0 limitation being closed in v1 (`X-Receipt-Schema: v1` header + server-side `tool_input` unpacking).

Workaround until v1: for tests that must exercise red-flag rules, POST events with flat `content`/`path` fields directly (see `scripts/smoke-us14.sh`).

### Session appears under the wrong user / missing when filtered

When the body omits `user`, the server attaches it from the bearer token. Two failure modes:

- Body carries a hardcoded `user` that differs from the token's owner → the body wins in v0. Remove `user` from the body, let the bearer decide.
- Wrong token in `~/.config/receipt/config.json` (e.g. a CI token instead of yours) → `receipt init --force` with your real email re-mints and rewrites config.

---

## Further reading

- Project-internal specs live under `vault/` — see `vault/EVENTIN-V1-SPEC.md` for the v1 schema migration and `vault/research/claude-code-hook-stdin-2026.md` for the canonical Claude Code stdin shape.
- `docs/API.md` — full backend endpoint reference.
- `docs/ARCHITECTURE.md` — hook → backend → dashboard data flow.

## Verification status

The following hook integrations have been verified:

| Agent | Parser unit tests | Backend ingestion tests | Local hook execution | Typecheck | Status |
|-------|------------------|------------------------|---------------------|-----------|---------|
| Claude Code | pass | pass | not run | N/A | Parser + backend verified |
| Codex | pass | pass | not run | pass | Parser + backend verified |
| OpenCode | N/A | pass | not run | unverified | Backend verified, plugin structure only |
| Cursor | pass | pass | not run | pass | Speculative (contract unverified) |

**Notes:**
- Parser unit tests verify the hook script can transform events correctly in isolation
- Backend ingestion tests verify the API accepts and stores events with the correct agent field
- Local hook execution requires running the actual agent environment (Claude Code, Codex, OpenCode, Cursor)
- OpenCode typecheck is unverified due to @opencode-ai/plugin dependency only available in OpenCode environment
- Cursor hook support is marked as speculative due to unverified contract assumptions
