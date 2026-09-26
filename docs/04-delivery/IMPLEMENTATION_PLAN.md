# Ordered delivery plan (App Maker)

**Current stage: demo and documentation baseline.** Ship existing interactive simulation on a feature branch; record exact SHA, unit/smoke checks, and owner demo feedback. Do not merge demo shortcuts into production as security architecture.

**F0:** formalize typed tool and connector SDK, choose implementation stack after repo review, schema/contract tests and CI; preserve demo separately. **F1:** identity/tenant/permissions/audit/secret references. **F2:** Telegram private text and secure pairing in test sandbox. **F3:** Google Drive/Google Sheets/XLSX read-only with approved test corpus and source provenance. **F4:** versioned client REST API read-only adapter using one sandbox system. **F5:** per-tenant LLM provider abstraction and safe tool orchestration. **F6:** robust CSV/XLSX/PDF reporting, source citations and access-limited artifacts. **F7:** Telegram voice notes STT/TTS, Dari evaluation. **F8:** cloud and local container packaging, network/backup/security UAT. **F9 (separate release):** per-system approved typed writes, domain validations and accounting safeguards.

For each milestone: map IDs from `ACCEPTANCE_CONTRACT.md` -> implement in isolated branch -> run unit/integration/security/e2e as applicable -> builder self-audit -> commit exact SHA -> independent Codex/other-agent audit same SHA -> remediate confirmed contract defects -> merge only after checks. No new criteria during audit except material safety/integrity blocker.

**First upcoming handoff:** demonstrate mock app, collect D-01–D-10 walkthrough feedback and pilot connector test inputs. If the owner explicitly authorizes documented read-only pilot, start F0/F1; real external integrations remain gated by provider credentials and approved test resources.
