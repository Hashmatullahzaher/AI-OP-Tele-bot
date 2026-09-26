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
| O-07 | **PARTIALLY RESOLVED 2026-09-26:** first actions are customer create/update, procurement request create and draft voucher create; draft voucher must balance and remain DRAFT. Still open: sanctioned customer write API, source-side actor/tenant + idempotency semantics, production step-up mechanism/expiry, customer-specific field rules and approval limits. | Full live F9 acceptance only; generic typed implementation/tests may proceed. |
| O-08 | **PARTIALLY RESOLVED 2026-09-26:** first UAT target is Windows PC / Windows Server. Still open: exact Windows versions/hardware baseline, production service packaging/account, cloud provider/region, network egress, secrets manager, backup RPO/RTO, monitoring and support/billing model. | Full F8 production certification. Windows UAT engineering may proceed. |
| O-09 | Native XLSX/PDF report requirements, voice STT/TTS provider, Dari evaluation set and report-delivery retention. | F6/F7 acceptance. |

Do not request or commit API keys, OAuth tokens, bot tokens, passwords or private customer documents. Missing official inputs block only their dependent feature.
