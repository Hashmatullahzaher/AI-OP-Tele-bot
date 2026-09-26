# Integration sequence and contracts

## G1 — Google Drive + spreadsheet read-only first live connector (proposed order)
Use customer-approved OAuth scopes and resource allowlist; enumerate only explicit shared/selected folders/files. Differentiate Google Sheets via Sheets API from XLSX file retrieval via Drive API. Never blindly fetch an entire Drive. Retain source ID, revision/modified date, worksheet, range, headers, currency and transformation steps. Drive API consent and test account are prerequisites. Cache only as explicitly approved; re-check source user/file ACL at each disclosure. A service account is appropriate only if the organization deliberately shares its resources and accepts the corresponding principal's access model.

## G2 — Customer REST API read-only connector
F4 implements a generic **contract**, not guessed customer routes. An administrator maps a sanctioned API specification into: one HTTPS origin, exact hostname allowlist, versioned operation aliases, configured GET path templates, allowed path/query parameters, source-auth provider and deterministic response-field mapping. The model/caller supplies only an operation alias plus values for predeclared parameters.

The transport rejects redirects, localhost/literal-IP source configuration and host escape. Unknown operations/parameters, schema mismatch and cross-tenant scope fail before or at the controlled source boundary. Source authorization must be derived from the trusted tenant/actor context so the upstream system retains its own role/tenant enforcement. No generic SQL, arbitrary URL, arbitrary path or write method exists.

A sanctioned customer OpenAPI/spec + sandbox remains required for live F4 E2E (O-03). Customer has no system? G1 alone suffices. Customer has a system? G2 is provisioned independently or combined with G1.

## Channel — Telegram
One tenant bot token or explicit per-tenant routing on a shared bot with isolated mapping, subject to owner choice. Bind to private chat, pair with company identity through out-of-band flow; verify webhook secret, deduplicate updates, throttle, revoke. Voice notes later use STT/TTS through same policy. Realtime voice calls are separately scoped.

## Contract / error codes (proposed)
`CAPABILITY_NOT_ALLOWED`, `IDENTITY_UNLINKED`, `SOURCE_ACCESS_DENIED`, `SOURCE_UNAVAILABLE`, `SOURCE_STALE`, `SOURCE_SCHEMA_AMBIGUOUS`, `PROVIDER_UNAVAILABLE`, `REPORT_EXPIRED`, `WRITE_REQUIRES_APPROVAL`. Every error must fail closed, avoid leaking source existence and attach an audit correlation ID to server-side event. No secret/client data is placed in the user-visible error.
