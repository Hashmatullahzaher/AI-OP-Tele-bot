# OS AI Core — universal AI operating layer

OS AI Core is a **standalone, reusable product** that connects authorized company sources such as Google Drive/Sheets/XLSX and versioned customer REST APIs to an AI orchestration layer, with Telegram text/voice and web/desktop channels. It is not dependent on Al-Biruni or any particular ERP.

## Current state

The `app/` directory contains the isolated synthetic presentation demo. The `osai/` package contains the production foundation:

- F0 typed capability registry, strict schemas and one local/cloud runtime contract;
- F1 tenant/actor policy, scoped resources, credential references and durable redacted audit;
- F3 read-only Google Drive/Sheets/XLSX connector code on `app-maker/f3-drive-readonly`.

The F3 connector uses real Google Drive v3 / Google Sheets v4 HTTPS APIs, server-configured aliases, per-disclosure metadata/access re-checks, bounded XLSX parsing and source provenance. It does **not** store OAuth credentials. Full live app-level Drive E2E remains blocked only by deployment of an OS AI Core OAuth client/token-custody implementation and private alias mapping (`O-02`).

Telegram, client REST API, LLM orchestration, voice, native XLSX/PDF report generation, permissioned writes and certified production deployment are not yet claimed.

### Run local tests

Python 3.11+:

```bash
python -m compileall -q app osai tests
python -m unittest discover -s tests -v
```

The browser demo can still be started with:

```bash
python -m app.server
# http://127.0.0.1:8765
```

Do not expose the demo publicly or use real company data in it.

## Confirmed direction

- Repository: `Hashmatullahzaher/AI-OP-Tele-bot`.
- Connector families in stages: Google Drive/spreadsheets first, then approved customer REST APIs.
- One backend core for cloud and customer-hosted/local deployments.
- Real Telegram/Google connections require internet access.
- The LLM never receives arbitrary SQL/URL/shell capability.
- Financial writes remain behind typed source-domain operations, source-system authorization, accounting invariants and explicit approval.

## App Maker source of truth

Read in this order:

1. `docs/04-delivery/ACCEPTANCE_CONTRACT.md`
2. `docs/00-governance/DECISIONS.md`
3. `docs/00-governance/OPEN_ITEMS.md`
4. `docs/03-architecture/SECURITY.md`
5. `docs/03-architecture/INTEGRATIONS.md`
6. `docs/04-delivery/IMPLEMENTATION_PLAN.md`
7. `prompts/BUILD_MASTER_PROMPT.md`

Never commit OAuth tokens, bot tokens, passwords, customer production data, private Drive IDs or private Drive URLs.

## Verification status

F0/F1 exact SHA `faf45ce316b1a6fbcdca25992c49d252259318a6` has green GitHub Actions and a lead-agent contract review result of `PASS_WITH_NONBLOCKING_NOTES`; the separate independent-auditor production gate is still retained.

F3 local builder verification currently passes **41/41 tests** plus `compileall`. Ruff/mypy must be confirmed by GitHub CI on the F3 exact SHA before the builder calls it ready for audit. See `docs/04-delivery/F3_DRIVE_EVIDENCE.md`.
