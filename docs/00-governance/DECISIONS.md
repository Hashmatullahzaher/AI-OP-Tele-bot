# Product-owner decision register

| ID | Decision | Status | Evidence / implication |
|---|---|---|---|
| DEC-001 | Authoritative existing repo is `Hashmatullahzaher/AI-OP-Tele-bot`. | CONFIRMED 2026-09-26 | Keep generic product separate from any individual customer ERP. |
| DEC-002 | Build both real connector families in stages: Google Drive/spreadsheets and client-system APIs. | CONFIRMED 2026-09-26 | One typed connector contract; first live slice Drive read-only, followed by API read-only when an example API contract is available. |
| DEC-003 | One shared core supports customer-hosted/local and cloud deployment. | CONFIRMED 2026-09-26 | One backend codebase; environment-specific packaging. Live Telegram/Google APIs require internet connectivity. |
| DEC-004 | Product is a universal AI operating layer, not an Al-Biruni bot. | CONFIRMED during discovery | A company with no ERP may connect approved Drive documents/spreadsheets. |
| DEC-005 | Telegram text/voice, source-grounded answers, Excel/PDF reports and permissioned changes are target capabilities. | CONFIRMED as product vision | Phase these capabilities; no financial write capability in the read-only pilot. |
| DEC-006 | Previous browser-based demo with two simulated connector families is preserved for product-direction review. | PROPOSED / demo only | It is not production evidence. |
| DEC-007 | First sanctioned live-source pilot is an owner-controlled Google Drive folder containing one native Sheet and one XLSX copy with synthetic data. | CONFIRMED 2026-09-26 | Private resource IDs, URLs and OAuth credentials stay outside Git. F3 remains read-only. |

| DEC-008 | First real UAT host target is Windows PC / Windows Server. | CONFIRMED 2026-09-26 | Keep one shared core; add Windows CI/UAT path before production service packaging. |
| DEC-009 | First F9 mutation scope is customer create/update, procurement request create, and draft voucher create only. | CONFIRMED 2026-09-26 | Posted voucher edit/post/delete remains prohibited; F9 uses approval, idempotency, source authorization and audit. |

Decision changes require author/date/rationale and affected acceptance criteria to be recorded here and in `OPEN_ITEMS.md` when applicable.
