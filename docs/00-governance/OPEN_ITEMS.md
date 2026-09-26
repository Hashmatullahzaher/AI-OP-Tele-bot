# Open items — authoritative blockers by feature

The owner approved the repository, **both connectors in stages**, and **one shared core for cloud and customer-hosted/local** deployment. These are closed; do not reopen them without a new owner decision. All other items below remain OPEN unless separately signed off.

| ID | Unresolved input | Blocks only |
|---|---|---|
| O-01 | Formal review/acceptance of the browser demo and exact pilot user journeys. | Demo gate / production feature sign-off; docs and independent tests may proceed. |
| O-02 | Google Workspace account owner, approved folder or test files, OAuth client setup/Drive scopes, token custody and revocation approach; use non-sensitive test data. | Live Drive connector end-to-end testing. |
| O-03 | One test client-system OpenAPI/spec, sandbox endpoint, read-only test account, tenant identity and documented permissions. | Live ERP/API connector integration. |
| O-04 | LLM provider, model, per-tenant privacy/data residency choices, token cost cap, whether text may leave customer host; language expectations. | Live LLM inference; typed mock orchestration can proceed. |
| O-05 | Telegram bot token, webhook/public HTTPS vs polling, private-chat user pairing/identity provider, approval of data classification for messages. | Real Telegram end-to-end testing and exposure of actual customer data. |
| O-06 | User roles, approved Drive ACL intersections, payroll/HR/customer PII handling, retention/deletion, audit retention and company-specific report sharing. | Production rollout with real sensitive data. |
| O-07 | For future writes, precise operations/approval limits/ledger invariants and authenticated out-of-band step-up process. | All production mutation capabilities. Read-only stays available. |
| O-08 | Hosting locations, target OS/hardware, cloud provider, local network egress, secrets manager, backup/restore, monitoring and support/billing model. | Deployment certification, not connector contracts. |
| O-09 | First-pilot native XLSX/PDF requirements, voice STT/TTS provider and Dari evaluation dataset, report delivery retention. | Native report and voice acceptance. |

Do not request API keys, Google tokens, bot tokens, passwords or private documents in chat. Store secrets in a vetted deployment secret store, not GitHub or environment samples with real values. Missing inputs must not be invented.
