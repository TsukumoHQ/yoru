# Licensing

Receipt is a **dual-licensed monorepo**, following the same pattern Supabase, PostHog, and Plausible use: a permissive client for easy embedding, a copyleft server that keeps commercial forks open.

## Summary (plain English)

| Part of Receipt | Path | License | What it means |
|---|---|---|---|
| **CLI client** | `receipt-cli/` | **MIT** | Install it anywhere. Embed it in internal tools, CI, closed-source forks. No obligation to share your changes. |
| **Co-located contract** | `packages/yoru-contract/` | **MIT** | CanonicalEvent schema + pairing wire types shared by the CLI and the server. MIT so it's safe to import from the MIT CLI once the CLI depends on it — an AGPL-adjacent contract would poison every install. Not yet vendored into or published with the CLI wheel; today it's a monorepo-only dev dependency of the CLI, and a normal dependency of the (never pip-installed) server. |
| **Server + dashboard + marketing site** | everything else at repo root | **AGPL-3.0** | Fork and run it for yourself — fine. Run it as a network service for other people? You must make your modified source available to those users. |

## Why this split

- **CLI MIT** — devs type `pip install receipt-cli` on work laptops, CI runners, and servers. A copyleft license on the CLI would poison every enterprise machine it touches and block adoption. MIT removes the legal friction so anyone can ship it.
- **Server AGPL** — the server is where the value lives. AGPL is what keeps a cloud provider from taking Receipt's code, running it as a competing SaaS, and closing off their improvements. If a provider wants to run a hosted Receipt competitor, they must share the code. We're fine with that.

This is **not** a "community edition / enterprise edition" trick — there is no crippled OSS version. The exact code we run at receipt.dev is in this repo, under these licenses.

## Self-hosting

You can self-host Receipt freely. The AGPL only kicks in if you **run the server as a service accessible to users other than yourself** and **modify the server code**. If you run the stock server on your own hardware for your own team, no additional obligation — you're just using free software.

If you fork the server and modify it for your users, AGPL requires you to give those users a way to get the modified source. A "View source" link in your dashboard footer is enough.

## File boundaries (authoritative)

- `receipt-cli/**` → MIT (see `receipt-cli/LICENSE`)
- `packages/yoru-contract/**` → MIT (see `packages/yoru-contract/LICENSE`)
- everything else → AGPL-3.0 (see `/LICENSE`)

Per-file SPDX headers aren't required but are welcomed for clarity:

```python
# SPDX-License-Identifier: MIT             (receipt-cli/, packages/yoru-contract/ files)
# SPDX-License-Identifier: AGPL-3.0-only   (server / frontend / marketing)
```

## Commercial use

- **Using the CLI inside your company** (MIT): zero restrictions.
- **Self-hosting the server for your own team** (AGPL internal use): zero restrictions.
- **Hosting Receipt as a service for paying customers** (AGPL network use): you must share your server modifications. Most people who want this just buy Receipt Cloud instead.
- **Embedding Receipt's CLI code in a proprietary CLI** (MIT): fine.
- **Embedding Receipt's server code in a proprietary product**: not compatible with AGPL — talk to us about a commercial license at `hello@opentruth.ch`.

## FAQ

**Can I use the CLI at work?** Yes. MIT. No strings.

**Can I self-host the server for my team?** Yes. AGPL internal use — no obligations.

**Can I fork and run Receipt as a paid SaaS?** Yes, but your modifications must be open-source under AGPL and made available to your users. Or buy a commercial license.

**Can I read the code?** Yes, it's all public in this repo.

**Why not MIT the whole thing?** So a cloud provider can't close up all the server-side improvements and ship a better Receipt without giving anything back.

**Why not AGPL the whole thing?** Because then large companies couldn't install the CLI on dev laptops without legal review — which would kill adoption.
