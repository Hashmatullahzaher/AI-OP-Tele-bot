# V1 plan — "Ask your business" (restructured 2026-09-27)

Owner: Hashmatullah Zaher (product owner). Project lead: Claude (senior director, appointed 2026-09-27).
This plan supersedes the F0–F9 sequencing in `docs/04-delivery/IMPLEMENTATION_PLAN.md` for v1. Earlier work is kept, not thrown away; parked items are listed below.

## 1. What v1 is

A Telegram bot that staff message in **Dari, Pashto or English, by text or voice**, asking questions about the company's data. It answers correctly, in the user's language, citing which sheet the numbers came from, and only from data that user's role is allowed to see.

- **First user:** our own company, on its real data.
- **Done means:** a **client-ready demo**. A prospective client watches a live Telegram conversation (text and voice) answered correctly from Google Sheets, with role restrictions visibly working. A separate demo company with synthetic data is used for client demos, so our real data is never shown.
- **Target:** 3 weeks from the owner's sign-off on this plan.
- **Budget:** under $50/month total (server, AI model and speech-to-text).

## 2. Scope

| In v1 | Parked (kept in Git history, tag `archive/pre-v1`) |
|---|---|
| Telegram private chat: text and voice notes | Write actions (F9: customers, procurement, draft vouchers) |
| Google Sheets / Drive, read-only | Excel sandbox UAT and Telegram → Excel write flow |
| Roles: which folders and sheets each role may see | Windows installer, Setup Wizard, Portable `.exe` |
| Pluggable AI model (cloud first; see D-2) | PDF reports (a CSV/XLSX answer attachment may stay) |
| Audit log, per-user pairing, cost limit | Fully local AI model (the interface stays; delivery is v2) |
| Cloud VPS deployment, backup, runbook | ERP REST connector: code kept; wiring is **v1.1** (see §5) |
| Demo company with synthetic data | Multi-customer billing and self-service onboarding |

**Why these cuts:** a 3-week, sub-$50 target with trilingual voice is only achievable if everything that doesn't serve "answer a question correctly" waits. The parked work has no users until Q&A works, and none of it is deleted.

## 3. What we keep from the existing code

- **Kept, and the base of v1:** `osai/contracts.py`, `storage.py` (tenants, roles, policy, audit), `connectors/google_drive.py`, `agent.py` (planner/orchestrator boundary), `channels/telegram.py`, `channels/telegram_voice.py`, `voice.py`, `runtime.py`, `backup.py`, and their tests. The safety rules stay non-negotiable: typed tools only, no SQL/URLs/shell for the model, and source data treated as data, never instructions.
- **Kept, dormant:** `connectors/rest_api.py`, which v1.1 wires to the ERP.
- **Parked:** `actions.py`, `action_approval.py`, `write_agent.py`, `connectors/rest_write.py`, `connectors/local_excel.py`, `excel_uat.py`, `telegram_excel_uat.py`, `channels/telegram_actions.py`, `setup_*`, `portable_app.py`, `windows_service.py`, `packaging/`.
- **Changed:** outside libraries are now allowed. The server is a cloud VPS, not a locked-down Windows PC, so the zero-dependency rule is lifted where a mature library removes risk. That covers the official AI SDKs, and `openpyxl` for XLSX files that aren't native Google Sheets.

## 4. Architecture for v1

```text
Telegram (long polling, no public HTTPS needed)
  -> pairing check: Telegram user -> actor -> tenant + role
  -> voice note? -> speech-to-text -> text
  -> AI planner: picks typed read tools (list sheets, read range, filter/aggregate)
  -> policy check: is this sheet inside the actor's role scope?
  -> Google Sheets/Drive read (service account; folders shared to it)
  -> deterministic Decimal arithmetic in code, never in the model
  -> answer in the user's language + source citation
  -> audit log
```

- **Google access:** a **service account**. The owner shares the chosen Drive folders with it. That replaces the OAuth consent/token-custody work in open item O-02 for our own company. Per-client OAuth comes back when we sell to others.
- **Hosting:** one small Linux VPS running Docker, SQLite and a nightly backup.

## 5. Schedule

| Week | Goal | Exit check |
|---|---|---|
| **0** (1–2 days) | **Consolidate.** Merge the 16-PR stack into `main`, fix the F3/F4 split, close obsolete PRs, tag `archive/pre-v1`, move parked modules out of the runtime path. Owner creates the accounts (§7). | `main` is green in CI and holds everything; one open branch. |
| **1** | **First real answer.** Real AI adapter, real bot on the VPS, real Sheets read. Build the evaluation set: 40–60 real questions with correct answers, in all three languages. | A staff member asks a real question on Telegram and gets the right number with its source. |
| **2** | **Correct and safe.** Handle messy real sheets (merged headers, Dari/Pashto column names, dates in the Solar Hijri calendar), roles to folders, user pairing, right-to-left formatting, voice notes. | Eval score ≥ 85% in Dari and English. A role cannot read outside its scope. Voice works in Dari. |
| **3** | **Client-ready.** Demo company, cost cap and usage report, deployment script, backup/restore drill, runbook, demo script. Team uses it daily. | Eval ≥ 90% (Dari/English); Pashto measured and reported. A full dry-run demo passes. Monthly cost projection under $50. |
| **v1.1** | ERP read-only through the existing REST connector. Can start in parallel once the ERP API documentation and a read-only test account arrive. | ERP questions answered with the same safeguards. |

## 6. Decisions the owner must make now

- **D-1 Model vs. budget.** At about 50 questions a day across the team, with roughly 15K input and 1.5K output tokens per question, the approximate model cost per month (before prompt-caching savings) is:
  - Claude Haiku 4.5: about $35
  - Claude Sonnet 5: about $70
  - Claude Opus 5: about $170

  Staying under $50 means Haiku-class, fewer questions, or raising the budget. **Recommendation:** start on Haiku 4.5 behind the pluggable interface, measure accuracy against the eval set in week 1, and escalate only if Dari/Pashto accuracy falls short. Server costs are extra, at about $5–10 a month.
- **D-2 AI provider.** "Customer chooses" is kept as an interface. V1 ships **one** cloud adapter; a local-model adapter is v2.
- **D-3 Pashto voice.** Speech-to-text for Pashto is weak across all providers today. Dari (close to Persian) is much better. **Recommendation:** v1 guarantees Dari and English voice, and Pashto voice is best effort with its accuracy measured and reported honestly. Pashto *text* is fully in scope.
- **D-4 Roles.** The owner lists the initial roles and which Drive folders each may see. For example: Owner = all; Finance = finance folder; Sales = sales folder.

## 7. What the owner provides (the lead supplies step-by-step guides)

Secrets go only into the server's environment file, **never** into chat or Git.

1. A small Linux VPS (Ubuntu 24.04, 1–2 GB RAM).
2. A Telegram bot created with @BotFather (the token goes on the server).
3. A Google Cloud project, a service account with the Sheets and Drive APIs enabled, and the chosen folders shared with that service account's email.
4. An API key for the AI model chosen in D-1, with a monthly spend limit set in the provider console.
5. A speech-to-text API key (the provider is chosen in week 1 by testing Dari/Pashto samples).
6. 40–60 real questions staff would ask, with the correct answers, to form the evaluation set.
7. ERP API documentation and a read-only test account (for v1.1).

## 8. How we run the project

- **One branch at a time.** Each change is a short-lived PR into `main`, with CI green before merge. No more 16-deep PR stacks.
- **Every Friday:** a status note covering what works, the eval score, cost to date, blockers and decisions needed.
- **Evidence over claims.** A feature is "done" when it passes the eval set live, not when unit tests pass.
