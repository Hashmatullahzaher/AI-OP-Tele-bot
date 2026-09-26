# Open items — authoritative blockers by feature

The repository, staged connector strategy, shared local/cloud core and owner-controlled Drive pilot corpus are confirmed. Do not reopen those decisions without a new owner decision.

| ID | Unresolved input | Blocks only |
|---|---|---|
| O-01 | Formal visual acceptance of the browser demo and exact presentation journeys. | Demo gate only; production foundation/connector engineering may proceed from the documented contract. |
| O-02 | Pilot folder/files are provisioned and validated. Remaining: OS AI Core OAuth client/consent with read-only Drive/Sheets scopes, actor/tenant token custody + refresh/revocation implementation, and deployment-only alias -> Drive ID mapping. | Full live app-owned Drive E2E acceptance. Connector implementation/tests and external corpus validation may proceed. |
| O-03 | One sanctioned client-system OpenAPI/spec, sandbox endpoint, read-only test account, tenant identity and documented permissions. | F4 live REST connector integration. |
| O-04 | LLM provider/model, per-tenant privacy/data-residency policy, token cost cap and whether content may leave customer host. | F5 live LLM inference. Typed orchestration can be built without choosing a provider. |
| O-05 | Telegram bot token, webhook/public HTTPS vs polling, private-chat user pairing/identity provider and message data classification. | F2 real Telegram E2E and any real customer data over Telegram. |
| O-06 | Production user-role matrix, Drive ACL intersection policy, PII/HR/payroll handling, retention/deletion, audit retention and report-sharing policy. | Production rollout with sensitive data. |
| O-07 | For future writes: exact operations, approval limits, ledger invariants and authenticated out-of-band step-up process. | F9 mutation capabilities only. Read-only remains available. |
| O-08 | Hosting locations, target OS/hardware, cloud provider, local network egress, secrets manager, backup/restore, monitoring and support/billing model. | F8 deployment certification. |
| O-09 | Native XLSX/PDF report requirements, voice STT/TTS provider, Dari evaluation set and report-delivery retention. | F6/F7 acceptance. |

Do not request or commit API keys, OAuth tokens, bot tokens, passwords or private customer documents. Missing official inputs block only their dependent feature.
