# Compliance surface

What yoru does for an audit, line by line. Every item is marked against the
code: **shipped** runs in the public beta today, **in progress** is landing now,
**planned** is on the roadmap and not yet built. A line only reads shipped once
the code backs it.

yoru is the record-keeping instrument, not legal advice. Whether your system is
high-risk under the EU AI Act is your determination to make; the article
references point to the obligation, not a warranty.

## Record-keeping (EU AI Act)

- [x] Automatic logging of every session, prompt, tool call and result — **Art. 12**
- [x] Retention under your control: self-hosted, your policy, your infrastructure — **Art. 19 · 26(6)**
- [x] Tamper-evident, hash-chained records — **Art. 73**

Source for the article mapping: [artificialintelligenceact.eu](https://artificialintelligenceact.eu).

## Integrity

- [x] Per-session hash-chain — each event commits to the one before it; alter a record and the chain stops verifying
- [x] Re-verify a whole session from the dashboard (and the API)
- [x] Append-only trail; secrets redacted at capture (content, output and tool input)

## Risk surface

- [x] Six red-flag categories: `secret`, `env`, `shell`, `db`, `migration`, `ci`
- [x] Per-developer attribution taken from the verified token — not self-claimed at ingest

## Control & residency

- [x] Self-hosted — audit data never leaves your infrastructure; you are the deployer
- [x] One-file JSON export of any session trail

## Access & identity

- [x] Organization roles: owner, admin, member
- [ ] Per-organization audit isolation (multi-tenant) — **(in progress)**
- [ ] SSO / SCIM — **(planned)**

## Evidence

- [ ] Per-organization signed EU AI Act evidence bundle — **(planned)**

---

Public beta. This page tracks the shipping surface; open an issue if a line
drifts from what the code does.
