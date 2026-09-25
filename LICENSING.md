# Licensing

yoru is a **dual-licensed monorepo**, following the same pattern Supabase, PostHog, and Plausible use: a permissive client for easy embedding, a copyleft server that keeps commercial forks open.

## Summary (plain English)

| Part of yoru | Path | License | What it means |
|---|---|---|---|
| **CLI client** | `yoru-cli/` | **MIT** | Install it anywhere. Embed it in internal tools, CI, closed-source forks. No requirement to share your changes — just keep the copyright/license notice with any copy you redistribute. |
| **Co-located contract** | `packages/yoru-contract/` | **MIT** | CanonicalEvent schema + pairing wire types shared by the CLI and the server. MIT so it's safe to import from the MIT CLI once the CLI depends on it — an AGPL-adjacent contract would poison every install. Not yet vendored into or published with the CLI wheel; today it's a monorepo-only dev dependency of the CLI, and a normal dependency of the (never pip-installed) server. |
| **Server + dashboard + marketing site** | everything else at repo root | **AGPL-3.0** | Fork and run it for yourself — fine. Run it as a network service for other people? You must make your modified source available to those users. |

## Why this split

- **CLI MIT** — devs type `pip install yoru-cli` on work laptops, CI runners, and servers. A copyleft license on the CLI would poison every enterprise machine it touches and block adoption. MIT removes the legal friction so anyone can ship it.
- **Server AGPL** — the server is where the value lives. AGPL is what keeps a cloud provider from taking yoru's code, running it as a competing SaaS, and closing off their improvements. If a provider wants to run a hosted yoru competitor, they must share the code. We're fine with that.

This is **not** a "community edition / enterprise edition" trick — there is no crippled OSS version, and there is no hosted yoru service. yoru is self-hosted only: the exact code in this repo, under these licenses, is the only thing that exists — you run it, on your own infrastructure.

## Self-hosting

You can self-host yoru under these license terms. The AGPL only kicks in if you **run the server as a service accessible to users other than yourself** and **modify the server code**. If you run the stock server on your own hardware for your own team, that internal use isn't a network-service trigger — you're just using free software under the AGPL's terms.

If you fork the server and modify it for your users, AGPL requires you to give those users a way to get the modified source. A "View source" link in your dashboard footer is enough.

## File boundaries (authoritative)

- `yoru-cli/**` → MIT (see `yoru-cli/LICENSE`)
- `packages/yoru-contract/**` → MIT (see `packages/yoru-contract/LICENSE`)
- everything else → AGPL-3.0 (see `/LICENSE`)

Per-file SPDX headers aren't required but are welcomed for clarity:

```python
# SPDX-License-Identifier: MIT             (yoru-cli/, packages/yoru-contract/ files)
# SPDX-License-Identifier: AGPL-3.0-only   (server / frontend / marketing)
```

## Commercial use

- **Using the CLI inside your company** (MIT): permitted, subject to MIT's copyright/license-notice requirement.
- **Self-hosting the server for your own team** (AGPL internal use): permitted; no network-service source-sharing obligation is triggered.
- **Hosting yoru as a service for paying customers** (AGPL network use): you must share your server modifications with those users, or arrange a separate commercial license.
- **Embedding yoru's CLI code in a proprietary CLI** (MIT): fine, subject to the same notice requirement.
- **Embedding yoru's server code in a proprietary product**: not compatible with AGPL — talk to us about a commercial license at `hello@yoru.sh`.

## FAQ

**Can I use the CLI at work?** Yes, under MIT — just keep the license/copyright notice with any redistributed copy.

**Can I self-host the server for my team?** Yes — AGPL internal use doesn't trigger the network-service source-sharing requirement.

**Can I fork and run yoru as a paid SaaS?** Yes, but your modifications must be open-source under AGPL and made available to your users, or you need a commercial license.

**Can I read the code?** Yes, it's all public in this repo.

**Why not MIT the whole thing?** So a cloud provider can't close up all the server-side improvements and ship a better yoru without giving anything back.

**Why not AGPL the whole thing?** Because then large companies couldn't install the CLI on dev laptops without legal review — which would kill adoption.
