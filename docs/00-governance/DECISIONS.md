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

| DEC-010 | Before a real customer API, exercise the same F9 flow against a local Excel sandbox workbook on Windows. | CONFIRMED 2026-09-26 | Excel is a UAT harness only; four approved actions write to controlled sheets with source-side idempotency. |

| DEC-011 | After direct Excel UAT, exercise Telegram ingress -> provider-neutral write planning -> separate same-actor approval -> F9 -> Excel before using live credentials. | CONFIRMED 2026-09-26 | CI uses a deterministic synthetic planner; live Telegram and live LLM remain separately gated. |

| DEC-012 | Windows UAT Setup Wizard stores local configuration in ProgramData and protects Telegram/OpenAI credentials with Windows DPAPI; Excel can activate immediately while live Telegram/LLM connectors remain gated. | CONFIRMED 2026-09-26 | Installer opens `/setup`; status APIs expose configured booleans only, never secret values. |

| DEC-013 | Provide a one-file Windows Portable Local mode that requires no installer, Windows Service, or administrator elevation. | CONFIRMED 2026-09-26 | Portable mode runs per-user on 127.0.0.1 using a dynamically selected free port; 8765/8766 are reserved, data stays under LocalAppData, and Dashboard/Setup/Stop are exposed from a small control window. |

| DEC-014 | Claude is appointed project lead (senior director); full authority to restructure the repository and merge to `main`, with owner review at milestones. | CONFIRMED 2026-09-27 | See `V1_PLAN.md`. |
| DEC-015 | V1 first user is the owner's own company; the v1 job is answering data questions (read-only). V1 is done when it is a client-ready demo. | CONFIRMED 2026-09-27 | Write actions (F9), Excel sandbox and Windows packaging are parked for v1, not deleted. |
| DEC-016 | V1 languages: Dari, Pashto and English, text and voice from day one. | CONFIRMED 2026-09-27 | Pashto voice accuracy risk is tracked as decision D-3 in `V1_PLAN.md`. |
| DEC-017 | V1 runs on a small cloud VPS; first data source is Google Sheets/Drive; ERP REST read-only follows in v1.1. | CONFIRMED 2026-09-27 | Supersedes DEC-008's Windows-first target for v1; Telegram uses long polling. |
| DEC-018 | AI model is customer-selectable behind a provider interface; v1 ships one cloud adapter, a local model is v2. | CONFIRMED 2026-09-27 | Model choice under the budget is decision D-1 in `V1_PLAN.md`. |
| DEC-019 | V1 has staff roles that restrict which data each person can see; target 2–3 weeks; running budget under $50/month; owner performs account setup from lead-provided guides. | CONFIRMED 2026-09-27 | Secrets live only in server environment files. |

Decision changes require author/date/rationale and affected acceptance criteria to be recorded here and in `OPEN_ITEMS.md` when applicable.
