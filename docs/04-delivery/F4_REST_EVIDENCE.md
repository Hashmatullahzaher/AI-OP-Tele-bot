# F4 client REST read-only — builder evidence

Status: **IMPLEMENTED TO SANCTIONED-API BOUNDARY; LIVE CUSTOMER API E2E BLOCKED BY O-03**.

Base SHA: `f50119eb82c0d3b0aadc638556f9c800acae4370` (F3 builder handoff).

## Implemented

- read-only `client_api.table.read` capability;
- source and operation aliases are configured server-side;
- caller/model cannot supply host, URL, route, HTTP method, header names, SQL or arbitrary response mapping;
- HTTPS-only source origin with exact hostname allowlist;
- literal-IP and localhost source configurations rejected;
- redirects refused;
- only configured GET routes may execute;
- configured path/query parameters have name, location, required/enum/length constraints;
- source authorization headers come from an injected provider using trusted `ExecutionContext`;
- tenant/source scope is checked before source I/O;
- bounded JSON responses only;
- configured result path and scalar field mapping fail closed on schema mismatch;
- API version and response revision metadata are attached as provenance;
- 401/403/404 become non-enumerating access denials; 429/5xx become source-unavailable failures;
- no mutation/POST/SQL escape hatch exists.

## Tests

`tests/test_rest_api_connector.py` covers registered route construction, path traversal, unknown operation/parameter, raw URL/SQL/method schema rejection, context-derived source auth, source-scope mismatch, 401/403/404 denial, redirect refusal, invalid response mapping, row limit, localhost/literal-IP rejection and cross-tenant policy denial before source I/O.

CI evidence is pending until the PR exact SHA runs.

## Exact remaining blocker for full F4 acceptance

`O-03`: provide one sanctioned customer-system OpenAPI/specification, sandbox HTTPS endpoint, read-only test identity, tenant/role semantics and expected response examples. These inputs will be converted to explicit operation aliases and response mappings; the system will not invent undocumented customer routes or permissions.
