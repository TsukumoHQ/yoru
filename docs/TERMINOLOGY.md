# Terminology

Receipt uses a handful of terms that look similar but mean different things. This doc disambiguates them.

> **Phase W (workspaces refactor)** introduced a new top-level entity: **Workspace**. Most user-facing copy now says "Workspace" instead of "Organization" — organizations still exist in the DB but are now purely a **billing + membership container** holding N workspaces.

## The three-axis confusion

Three independent axes that often get conflated:

1. **Plan** (billing tier) — Free / Pro / Team / Org. Gates features and quotas.
2. **Organization** (shared container) — `organizations` row. Holds members via `organization_members`. Consumes seats. Contains N workspaces.
3. **Workspace** (session destination) — `workspaces` row. Personal (owner-user only) or team (inside an org). Where sessions land.

A user Free can have 1 personal workspace AND be a member of a Team-plan org with its own workspaces — the two worlds don't interact.

## Glossary

| Term | Where it lives | Definition |
|---|---|---|
| **User** | `auth.users` (Supabase) | A human account. |
| **Workspace** | `workspaces` (Supabase, NEW) | The session-destination entity. `org_id=NULL` → personal; `org_id=X` → team (inside org X). Every user has at least one personal workspace, auto-created on signup by the `on_auth_user_created_personal_workspace` trigger. |
| **Workspace repo mapping** | `workspace_repos` (Supabase, NEW) | Tells routing: "sessions with `git_remote=owner/repo` go to workspace W". One repo → one workspace (UNIQUE constraint). |
| **Organization** | `organizations` (Supabase) | A shared container. Has `organization_members`. Holds N workspaces. Created by a user and can be promoted from a personal workspace. |
| **Seat** | `organization_members` (1 row) | A user's membership in an org. Seat count = `COUNT(*) FROM organization_members WHERE org_id = X`. Billing axis for Team/Org plans. |
| **GitHub integration** | `github_integrations` (Supabase, NEW) | Stores the user's GitHub OAuth provider_token (via Supabase Auth's GitHub provider). Used to list repos and auto-map to workspaces. |
| **Plan "Free"** | `plans.name = 'Free'` | $0. 1 seat. 5k events/mo. 1 personal workspace. |
| **Plan "Pro"** | `plans.name = 'Pro'` | $9/mo. 1 seat. 25k events/mo. 5 personal workspaces. |
| **Plan "Team"** | `plans.name = 'Team'` | $19/seat/mo. 2–20 seats. 100k events/seat/mo pooled. Unlimited personal workspaces. |
| **Plan "Org"** | `plans.name = 'Org'` | $99 base + $15/seat/mo. Unlimited seats. SSO, SCIM, signed audit export. |
| **Subscription** | `subscriptions` | The live contract between a user and a plan. |
| **Feature** | `features` / `plan_features` / `user_grants` | Yoru feature catalog. `workspaces_max` (quota), `github_integration` (flag), `seats_max`, `events_per_month`, `retention_days`. Resolved via `user_grants → plan_features → default_value`. |
| **CLI user token** | `cli_tokens.token_type = 'user'` | `rcpt_u_*`. Minted by device-code pairing. Bound to a human. Dies if human leaves all orgs. |
| **CLI service token** | `cli_tokens.token_type = 'service'` | `rcpt_s_*`. Minted by an org admin. `workspace_id` set. Survives user departures. For CI/servers/fleet. |
| **Event** | `events` (SQLite) | One Claude Code hook firing. Has `cwd`, `git_remote`, `git_branch`, `kind`, `tool`, `content`, `flags`, `raw`. |
| **Session** | `sessions` (SQLite) | A Claude Code session. Aggregates events. `workspace_id` is frozen at first event via `resolve_workspace` RPC. |
| **Route rule** | `route_rules` (Supabase) | Escape-hatch for non-GitHub or self-hosted git: `(match_pattern, match_type)` → `target_workspace_id`. For 99% of users, `workspace_repos` covers the need. |
| **Routing destination** | `sessions.workspace_id` (SQLite) | The workspace this session's events belong to. `NULL` only if routing couldn't resolve (transient Supabase outage). |

## Deprecated terms

- **`organizations.type`** — The `'personal' | 'team'` column on `organizations` is deprecated post-W1. All personal-type orgs were migrated to `workspaces` with `org_id=NULL`. `'team'` orgs still exist as shared containers but the column will be dropped in a future cleanup. Do not branch on it in new code.
- **`hook_tokens`** table — renamed to `cli_tokens` in Phase B. The rename is idempotent-migrated at startup.
- **`resolve_route_rule` RPC** — replaced by `resolve_workspace` in Phase W1. Returns a workspace_id instead of an org_id.
- **`sessions.org_id`** — renamed to `sessions.workspace_id` in Phase W1. Values were backfilled via the `migration_workspace_id` mapping on `organizations.settings`.

## Routing resolution order

At first event of a session, the server calls `resolve_workspace(user_email, cwd, git_remote)` which checks:

1. **`workspace_repos` match** on parsed `(host, owner, repo)` from `git_remote`. Primary path. Populated by the GitHub Connect flow or manual mapping.
2. **`route_rules` glob match** on `cwd` or `git_remote` patterns. Escape-hatch for self-hosted git.
3. **User's first personal workspace** as fallback.

This order means: if you connect GitHub and map a repo, it overrides any old `route_rules` you had for the same path.

## Orthogonality matrix

All 6 combinations are valid:

| Plan | Has an org? | What it means |
|---|---|---|
| Free | No | Solo dev, all sessions land in personal workspace. |
| Free | Yes | User is a member of someone else's team/org. They can't create more workspaces themselves. |
| Pro | No | Solo dev with up to 5 personal workspaces. |
| Pro | Yes | Same, but also member of a team. |
| Team | Yes | Owns an org with 2–20 seats. Many team workspaces inside. |
| Org | Yes | Enterprise tier. |

## How seat-counting works

```
seats_used(org_X) = COUNT(*) FROM organization_members WHERE org_id = X
```

Invitations count as seats only once accepted. Pending invites don't consume a seat.

## How plan limits apply

Limits (events/mo, retention_days, workspaces_max) apply **per destination**, not per user:

- Event routed to workspace in org X (Team plan) → counted against X's Team pool.
- Event routed to user U's personal workspace (Free plan) → counted against U's Free cap (5k/mo).

A Free user invited into a Team org gets routing for free — the ORG's plan pays.

## Tokens vs seats

**Tokens are unlimited per user and per org.** Only seats (humans) are billed. Install the CLI on 20 laptops under one subscription — the event cap is the natural ceiling.

See [`.agents/cli-auth-routing-plan.md`](../.agents/cli-auth-routing-plan.md) for the authoritative Phase W architecture. See [`LICENSING.md`](../LICENSING.md) for the dual-license rationale.
