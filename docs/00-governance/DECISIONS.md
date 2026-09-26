# Product-owner decision register

| ID | Decision | Status | Evidence / implication |
|---|---|---|---|
| DEC-001 | Authoritative existing repo is `Hashmatullahzaher/AI-OP-Tele-bot`. | CONFIRMED 2026-09-26 | Keep generic product separate from any individual customer ERP. |
| DEC-002 | Build both real connector families in stages: Google Drive/spreadsheets and client-system APIs. | CONFIRMED 2026-09-26 | One typed connector contract; first live slice Drive read-only, followed by API read-only when an example API contract is available. Exact connector order for production may be adjusted with owner approval. |
| DEC-003 | One shared core supporting customer-hosted/local and cloud deployment. | CONFIRMED 2026-09-26 | One backend codebase; environment-specific packaging, secure outbound bridge for offline/LAN sources. Live Telegram and Google APIs need internet connectivity. |
| DEC-004 | Product is a universal AI operating layer, not an Al-Biruni bot. | CONFIRMED during discovery | ERP-less company may connect approved Drive documents/spreadsheets. |
| DEC-005 | Telegram text/voice, source-grounded answers, Excel/PDF reports and permissioned changes are target capabilities. | CONFIRMED as product vision, NOT approved as MVP implementation scope | Phase these capabilities; no financial write capability in read-only pilot. |
| DEC-006 | Previous browser-based demo with two simulated connector families can be preserved for owner review. | PROPOSED | Demo not yet formally accepted; do not claim it meets production acceptance. |

Decision changes require author, date, rationale and affected acceptance criteria in this file and `docs/00-governance/OPEN_ITEMS.md`.
