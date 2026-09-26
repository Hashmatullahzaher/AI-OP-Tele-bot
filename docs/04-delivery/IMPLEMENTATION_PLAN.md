# Ordered delivery plan (App Maker)

**Current stage: F6 source-linked reporting.** F0/F1 regression remediation is present in the current lineage; F2–F5 builder slices have green CI. Live provider/source E2E gates remain separately tracked in OPEN_ITEMS and do not justify inventing credentials or customer policies.

Milestones:

- **F0 — Shared foundation:** typed tool/connector SDK, schema contracts, local/cloud runtime contract, CI.
- **F1 — Tenant identity/policy/audit:** tenant/actor/grants, scoped objects, secret references, durable redacted audit.
- **F2 — Telegram private text:** secure pairing, private-chat routing, webhook/polling boundary, revoke/replay controls. Live E2E is blocked by O-05.
- **F3 — Google Drive read-only:** Sheets/XLSX aliases, per-disclosure source re-check, bounded parser and provenance. Builder handoff SHA `f50119eb82c0d3b0aadc638556f9c800acae4370`; O-02 blocks full app-owned OAuth E2E only.
- **F4 — Client REST read-only:** typed versioned GET adapter, exact host/operation allowlists, source-auth boundary and deterministic response mapping. Builder handoff SHA `da4bccf5519386eb68b33215e09fba87131e9720`; O-03 blocks sanctioned-customer live E2E only.
- **F5 — Source-grounded AI:** provider abstraction, strict single-tool planning, policy, source provenance, deterministic financial analysis and mandatory audit. Current branch implements this to the provider-adapter boundary; O-04 blocks the live model run.
- **F6 — Reports:** deterministic source-linked CSV/native XLSX/PDF artifacts, expiry/integrity metadata, and tenant/actor re-check at download. Current branch implements the generic artifact layer; O-09 controls pilot layout, Unicode/Dari PDF font packaging and retention/delivery policy.
- **F7 — Voice:** Telegram voice notes STT/TTS through identical auth/policy/audit.
- **F8 — Dual deployment:** same core/version in cloud and local packaging, backup/restore and network hardening.
- **F9 — Permissioned writes (separate release):** typed domain operations, step-up approval, idempotency and financial invariants.

For each milestone:

`MAP CONTRACT -> IMPLEMENT -> TEST -> SELF-AUDIT -> FIX -> COMMIT EXACT SHA -> CI -> INDEPENDENT AUDIT -> REMEDIATE -> MERGE`

The auditor must use the same `docs/04-delivery/ACCEPTANCE_CONTRACT.md`. New audit blockers outside that contract are limited to material security, privacy, data-loss, financial-integrity, unrecoverable-correctness or severe operational failures.

Missing official inputs block only their dependent live feature. Generic contracts, negative tests and unrelated milestones continue without inventing customer routes, permissions, credentials or policy.
