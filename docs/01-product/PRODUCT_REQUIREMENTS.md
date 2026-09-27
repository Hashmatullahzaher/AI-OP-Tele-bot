# Product requirements — proposed production baseline

Status: DRAFT for owner review; `DECISIONS.md` records confirmed strategic decisions, but demo UX and pilot scope await owner acceptance. Requirements below are candidates for the acceptance contract, not a claim of implemented features.

| ID | Behavior and observable outcome | Initial phase |
|---|---|---|
| PR-01 | Tenant admin creates isolated tenant/deployment and activates one or more connector instances without altering the shared core. | F1 |
| PR-02 | Company user links a verified identity to a Telegram private chat via expiring one-use pairing; revoked users receive no results. | F2 |
| PR-03 | A user asks a question; agent selects only registered typed READ capabilities; the service checks tenant, actor and document/resource permissions before reading. | F3–F5 |
| PR-04 | Google Drive connector lists/reads only explicitly shared/approved file IDs or folder resources and supported Sheet/XLSX ranges; records file ID, name, modified timestamp, tab/range and units where available. | F3 |
| PR-05 | REST connector calls only a customer-approved OpenAPI-derived or mapped read-only endpoint, with server-side policy; no arbitrary URL/SQL/query from LLM. | F4 |
| PR-06 | Results show human-readable source, source timestamp/freshness, applicable filters, currency/unit and data coverage; missing access, stale files and ambiguous columns fail visibly. | F3–F5 |
| PR-07 | Excel/PDF report exports use deterministic calculations and preserve provenance and access check at download; early pilot may ship CSV before native XLSX/PDF, explicitly labeled. | F5–F7 |
| PR-08 | Each attempted tool call and denied action creates tamper-evident audit metadata with tenant, actor, capability, resource scope and correlation ID; sensitive raw payloads are not logged. | F1–F5 |
| PR-09 | Local and cloud deployments share source/core and conformance tests; local sources require secure egress bridge where applicable; fully offline mode expressly disables live Telegram/Drive. | F0, F8 |
| PR-10 | Voice notes pass through STT -> same identity/capability policy -> optional TTS; no separate authorization bypass. | F7 |
| PR-11 | Any future write uses a typed operation, server-side domain service, existing business validations and independent user confirmation; accounting posting invariants must not be delegated to LLM. | F9, not in read-only pilot |

Non-functional: no tenant crossover in API, cache, model prompt, logs, reports or connector credentials. Define latency/cost/uptime targets with the owner before final production acceptance. Accessible Dari RTL/English interface is a product direction, not a guarantee of voice recognition accuracy.
