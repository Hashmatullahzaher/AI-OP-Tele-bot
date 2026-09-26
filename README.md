# OS AI Core — universal AI operating layer

OS AI Core is a **standalone, reusable product** that connects authorized company sources such as Google Drive/Sheets/XLSX and versioned customer REST APIs to a policy-constrained AI orchestration layer, with Telegram text/voice and web/desktop channels. It is not dependent on Al-Biruni or any particular ERP.

## Current production-foundation slices

The `app/` directory remains an isolated synthetic presentation demo. Production-oriented code lives under `osai/`:

- **F0:** typed capability registry, strict schemas and one local/cloud runtime contract;
- **F1:** tenant/actor policy, scoped resources, credential references and durable redacted audit;
- **F3:** read-only Google Drive/Sheets/XLSX connector;
- **F4:** typed read-only customer REST API connector;
- **F5:** provider-neutral, source-grounded AI planner/orchestrator with deterministic analysis;
- **F6:** tenant-scoped source-linked CSV/XLSX/PDF report artifacts with expiry and integrity checks;
- **F7:** Telegram voice-note/STT/TTS provider boundary through the same identity and audit controls;
- **F8:** shared local/cloud runtime, readiness and backup/restore with Windows as the first UAT target;
- **F9:** permissioned customer create/update, procurement request create and balanced draft-voucher create with approval + idempotency.
- **Excel Sandbox UAT:** a local XLSX fake-client source for Windows testing of the same four actions before a real customer API is connected.
- **Telegram → Excel orchestration UAT:** paired private Telegram input, provider-neutral write proposal, separate payload-bound approval, then the same F9 controls and XLSX mutation. CI uses a deterministic synthetic planner until a live LLM is approved.
- **Windows Setup Wizard UAT:** installable loopback dashboard can create the managed Excel workbook and securely store Telegram/OpenAI credentials using Windows DPAPI while keeping live connectors disabled until their acceptance gates pass.
- **Windows Portable Local UAT:** one-file `OS-AI-Core-Portable.exe` mode requiring no installation, Windows Service, or administrator rights; it selects a free `127.0.0.1` port automatically (never 8765/8766) and provides Dashboard, Setup, Data Folder, and Stop controls.

F3 and F4 have exact-SHA green CI builder handoffs. Their sanctioned live external E2E tests remain gated by deployment inputs O-02 and O-03. F5 is being built to the model-provider boundary; O-04 controls the live LLM selection and data/privacy policy.

Telegram, Drive, reports, voice, deployment and F9 write foundations are implemented to their documented credential/customer-policy boundaries. No live customer write integration or production certification is claimed until the remaining OPEN_ITEMS gates are satisfied.

## Safety architecture

The AI does **not** receive arbitrary SQL, URLs, shell access or database credentials.

```text
user
 -> provider proposes one strict read-only plan
 -> server validates typed arguments
 -> server authorizes trusted tenant/actor
 -> typed Drive or REST connector
 -> source provenance
 -> deterministic Decimal analysis
 -> durable audit
 -> grounded answer
```

Source content is untrusted data, not instructions. In the current F5 foundation it is never recursively fed back to the planner, preventing a spreadsheet/API cell from causing a second tool invocation.

F9 permits only four owner-approved action classes. Draft vouchers must balance and remain DRAFT; posting, deletion and editing posted vouchers remain prohibited.

## Local verification

Python 3.11+:

```bash
python -m compileall -q app osai tests
python -m unittest discover -s tests -v
```

The synthetic browser demo can still be started with:

```bash
python -m app.server
# http://127.0.0.1:8765
```

Do not expose the demo publicly or use real company data in it.

For the Windows Excel sandbox UAT, see `docs/05-operations/EXCEL_UAT.md`. For the Telegram planning/approval layer, see `docs/05-operations/TELEGRAM_AI_EXCEL_UAT.md`. For the installable configuration layer, see `docs/05-operations/WINDOWS_SETUP_WIZARD_UAT.md`. For the no-install local mode, see `docs/05-operations/WINDOWS_PORTABLE_LOCAL_UAT.md`.

## App Maker source of truth

Read:

1. `docs/04-delivery/ACCEPTANCE_CONTRACT.md`
2. `docs/00-governance/DECISIONS.md`
3. `docs/00-governance/OPEN_ITEMS.md`
4. `docs/03-architecture/SECURITY.md`
5. `docs/03-architecture/INTEGRATIONS.md`
6. `docs/03-architecture/AI_AGENT.md`
7. `docs/04-delivery/IMPLEMENTATION_PLAN.md`
8. `prompts/BUILD_MASTER_PROMPT.md`

Never commit OAuth tokens, bot tokens, passwords, customer production data, private Drive IDs or private Drive URLs.
