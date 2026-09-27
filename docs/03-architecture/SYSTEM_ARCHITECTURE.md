# Target system architecture — draft, same core in two deployments

```text
Private Telegram text/voice    Browser/Windows UI (future)
             |                          |
        Channel adapters + verified identity/session
                           |
                 Tenant & policy gate
                           |
       AI orchestrator -> LLM provider abstraction
                           |
           Typed capability registry / executor
                     |             |
              Drive adapter     Client REST adapter
                     |             |
             Google Drive     Customer domain APIs
                     \             /
             provenance + deterministic reporting
                           |
               authorized answer / artifact
```

- **Shared core:** one versioned runtime and connector SDK used both on customer-hosted server/PC and managed cloud. Local Windows shell, if built, is only UI/installer; it does not fork AI business logic. Avoid requiring cloud control-plane access for local-only processing where source/channel permits.
- **Deploy profiles:** `cloud`: cloud-managed ingress/webhook/worker, tenant isolation, managed database/secrets, backup and audit; `local`: bound-to-loopback admin, OS-bound secret store, local database and controlled outbound HTTPS access. When local system is LAN-only, use a mutually authenticated outbound relay with tenant-scoped service identity; no direct public ERP/SQLite exposure. Offline operation excludes live Telegram and remote Google APIs.
- **Provider abstraction:** independently configurable LLM provider per tenant/deployment, explicit disclosure policy on what data can leave the host. Provider failure does not bypass policy; deterministic capability execution remains independent of model selection.
- **Drive ingest:** approved consent + resource allowlist and source ACLs, file listing on demand, Sheets API for native Sheets; Drive export/download with explicit format handling for XLSX/CSV/document text. Parsing is sandboxed with strict file size and row limits. Store only necessary extracted snippets and metadata under tenant-scoped retention; re-check authorization when replying/exporting.
- **REST ingest:** admin-supplied, verified hostname, TLS, per-tenant secret reference, versioned capability-route map, allowlisted paths/methods, schema translation and timeout/retry policy. Backend must protect against SSRF and untrusted redirects. Source system must perform its own auth and accounting logic.
- **Reporting:** use deterministic Decimal/typed validation for monetary data, preserve currency, period and source provenance; immutable time-limited download URL guarded by server-side permission checks.
- **Audit:** append-only persistent events, redacted payloads, request correlation, denial events and credential/permission revocations; audit design/retention reviewed before real data.

This document states desired behavior, NOT evidence of current implementation. The checked-in demo only runs locally with mock connectors, keyword routing and in-memory audit.
