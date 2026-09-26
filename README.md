# OS AI Core — universal AI operating layer

OS AI Core is a **standalone, reusable product** to connect authorized company sources such as Google Drive/Sheets/XLSX and versioned customer REST APIs to an AI orchestration layer, with Telegram text/voice and web/desktop channels. It is *not* dependent on Al-Biruni or any particular ERP.

## Current state — F0/F1 foundation remediated; F3 Drive connector implemented to OAuth boundary

The `app/` directory contains a **local, synthetic** browser mock. The `osai/` package contains the production-oriented F0/F1 foundation plus the F3 read-only Google Drive/Sheets/XLSX connector. F1 now has explicit tenant-user, tenant-admin and platform-operator separation. F3 is implemented through an injected OAuth access-token boundary with alias-only resources, per-disclosure metadata/ACL re-checks, bounded XLSX parsing and provenance. There is still **NO app-owned live OAuth credential, Telegram, ERP API, LLM, voice, native XLSX/PDF report pipeline, or certified production deployment**. The owner-controlled Drive pilot corpus is synthetic and its private IDs/tokens are deliberately not committed.

### Run the demo (Python 3.10+, standard library only)

```bash
python -m app.server
# open http://127.0.0.1:8765
python -m unittest discover -s tests -v
```

Demo: synthetic September sales/expenses and mock projects/accounts; source-grounded answers and Excel-compatible **CSV**, not native XLSX. Write attempts are denied, audit lives only in memory.

## Confirmed direction

- Existing home: `Hashmatullahzaher/AI-OP-Tele-bot`.
- Both connector families **in stages**: Google Drive/spreadsheets and approved customer REST APIs.
- **One backend core** packaged for cloud and customer-hosted/local use, not two separate AI products.
- Real Telegram/Google connections require internet; a completely offline host cannot offer those live channels.
- Client-system financial writes remain behind approved, typed domain-service operations, accounting invariants and source-system permission checks.

## App Maker source of truth

Start with `docs/00-governance/DECISIONS.md`, `docs/00-governance/OPEN_ITEMS.md`, `docs/00-demo/DEMO_FEEDBACK.md`, `docs/04-delivery/ACCEPTANCE_CONTRACT.md`, `docs/04-delivery/IMPLEMENTATION_PLAN.md` and `prompts/BUILD_MASTER_PROMPT.md`. All production docs are **proposed** pending owner demo/pilot validation. Never commit tokens or client records.

## Foundation verification

Current local builder verification: `python -m compileall -q app osai` and `python -m unittest discover -s tests -v` -> **43/43 tests pass**. F1 remediation SHA `b61d1e75a2c830bfaca4b0277c29d6bfa3006a35` passed GitHub Actions run `36217089400`. F3 still requires green CI on its own exact SHA and app-owned OAuth E2E before full acceptance.
