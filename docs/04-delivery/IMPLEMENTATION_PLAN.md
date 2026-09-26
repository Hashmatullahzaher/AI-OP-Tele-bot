# Ordered delivery plan (App Maker)

**Current stage: F3 Google Drive read-only implementation.** F0/F1 initial SHA `faf45ce316b1a6fbcdca25992c49d252259318a6` failed one acceptance detail (operator-vs-tenant role separation). Remediation SHA `b61d1e75a2c830bfaca4b0277c29d6bfa3006a35` adds explicit roles and passed GitHub Actions run `36217089400`. The isolated browser demo is not production evidence.

Milestones:

- **F0 — Shared foundation:** typed tool/connector SDK, schema contracts, local/cloud runtime contract, CI.
- **F1 — Tenant identity/policy/audit:** tenant/actor/grants, scoped objects, secret references, durable redacted audit.
- **F2 — Telegram private text:** secure pairing, private-chat routing, webhook/polling boundary, revoke/replay controls.
- **F3 — Google Drive read-only:** Google Sheets/XLSX aliases, per-disclosure source re-check, bounded parser, provenance. Current branch implements this through the OAuth/token-provider boundary; O-02 blocks only live app-owned OAuth E2E.
- **F4 — Client REST read-only:** typed versioned API adapter against one sanctioned sandbox system.
- **F5 — Source-grounded AI:** provider abstraction and policy-constrained typed tool orchestration.
- **F6 — Reports:** CSV then approved native XLSX/PDF with source provenance and access-limited artifacts.
- **F7 — Voice:** Telegram voice notes STT/TTS through identical auth/policy/audit.
- **F8 — Dual deployment:** same core/version in cloud and local packaging, backup/restore and network hardening.
- **F9 — Permissioned writes (separate release):** typed domain operations, step-up approval, idempotency and financial invariants.

For each milestone:

`MAP CONTRACT -> IMPLEMENT -> TEST -> SELF-AUDIT -> FIX -> COMMIT EXACT SHA -> CI -> INDEPENDENT AUDIT -> REMEDIATE -> MERGE`

The auditor must use the same `docs/04-delivery/ACCEPTANCE_CONTRACT.md`. New audit blockers outside that contract are limited to material security, privacy, data-loss, financial-integrity, unrecoverable-correctness or severe operational failures.

**Next handoff:** obtain green CI for the F3 exact SHA. If green, mark the builder package `READY_FOR_INDEPENDENT_AUDIT`. Full F3 acceptance waits only on O-02 live app-owned OAuth/token-custody E2E. F4 may begin in parallel only after O-03 supplies a sanctioned API contract.
