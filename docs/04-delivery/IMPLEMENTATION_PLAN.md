# Ordered delivery plan (App Maker)

**Current stage: Windows installable Setup Wizard over the proven Excel/Telegram orchestration UAT.** The installer now exposes a loopback-only configuration surface that can create the managed Excel workbook and securely store Telegram/OpenAI credentials using Windows DPAPI. Live Telegram/LLM network activation remains gated by O-04/O-05.

Milestones:

- **F0 — Shared foundation:** typed tool/connector SDK, schema contracts, local/cloud runtime contract, CI.
- **F1 — Tenant identity/policy/audit:** tenant/actor/grants, operator separation, secret references and durable redacted audit.
- **F2 — Telegram private text:** secure pairing, private-chat routing, webhook boundary, revoke/replay controls. Live E2E remains gated by O-05.
- **F3 — Google Drive read-only:** Sheets/XLSX aliases, ACL/resource re-check, bounded parsing and provenance. App-owned OAuth E2E remains gated by O-02.
- **F4 — Client REST read-only:** typed GET adapter, host/operation allowlists and source-auth boundary. Live customer sandbox remains gated by O-03.
- **F5 — Source-grounded AI:** provider abstraction, strict tool planning, provenance, deterministic financial analysis and mandatory audit. Live model selection remains gated by O-04.
- **F6 — Reports:** source-linked CSV/XLSX/PDF artifacts with expiry, integrity and access re-check. Pilot layout/Dari PDF policy remains under O-09.
- **F7 — Voice:** Telegram voice notes STT/TTS through the same auth/policy/audit. Live provider + Dari evaluation remains under O-09.
- **F8 — Dual deployment:** same core for cloud/local, readiness and backup/restore. First UAT host is now Windows PC / Windows Server; production certification details remain O-08.
- **F9 — Permissioned writes:** current owner-approved scope is customer create/update, procurement request create and **draft voucher create only**. Every write requires explicit capability authorization, deterministic domain validation, step-up approval, idempotency, source actor/tenant authorization and durable audit. Posted voucher mutation remains prohibited.

**Windows Setup Wizard UAT (owner-sanctioned packaging layer):** localhost-only `/setup` persists validated non-secret configuration, creates the managed Excel sandbox, and stores Telegram/OpenAI credentials through Windows DPAPI without returning secret values. The UI must not claim live Telegram or LLM connectivity until their separately gated integration tests pass.

For each milestone:

`MAP CONTRACT -> IMPLEMENT -> TEST -> SELF-AUDIT -> FIX -> COMMIT EXACT SHA -> CI -> INDEPENDENT AUDIT -> REMEDIATE -> MERGE`

F9 live acceptance additionally requires a sanctioned source write API for the four actions and a production step-up approval mechanism. Missing client-specific rules must not be invented.

The auditor must use the shared acceptance contract plus `docs/04-delivery/F9_ACTION_CONTRACT.md`. New audit blockers outside those contracts are limited to material security, privacy, data loss, financial integrity, unrecoverable correctness or severe operational failure.
