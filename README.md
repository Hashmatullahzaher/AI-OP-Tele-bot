# OS AI Core — universal AI operating layer

OS AI Core is a **standalone, reusable product** to connect authorized company sources such as Google Drive/Sheets/XLSX and versioned customer REST APIs to an AI orchestration layer, with Telegram text/voice and web/desktop channels. It is *not* dependent on Al-Biruni or any particular ERP.

## Current state — phase 0 presentation demo only

This repository currently contains a **local, synthetic** browser mock of Telegram, spreadsheet data and a client API. There is **NO live Telegram, Google Drive, ERP API, LLM, OAuth, real RBAC, voice, native XLSX/PDF, or production cloud/local deployment**. Do not expose the mock server publicly or use real company data. A previously shared demo is preserved here to support owner review; the product-owner decisions have been recorded, but the demo walkthrough is still outstanding.

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
