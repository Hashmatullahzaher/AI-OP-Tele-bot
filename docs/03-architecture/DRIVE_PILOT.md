# Google Drive read-only pilot — sanctioned test corpus

Status: **OWNER-APPROVED TEST SOURCE; F3 CONNECTOR IMPLEMENTED THROUGH TOKEN-PROVIDER BOUNDARY; APP OAUTH CREDENTIAL PROVISIONING STILL OPEN** (2026-09-26).

The owner selected an owner-controlled Google Drive folder for the first real-source pilot. The folder contains only synthetic/non-sensitive test data and currently includes:

- one native Google Sheet named `OS AI Core Pilot - Finance & Operations`;
- one XLSX export of the same corpus named `OS AI Core Pilot - Finance & Operations.xlsx`;
- tabs/data domains for `Transactions`, `Accounts`, `Projects`, and `Customers`;
- AFN-only synthetic figures suitable for deterministic read tests.

The sanctioned account can list/read the pilot corpus, and the F3 implementation now supplies alias-only resource access, per-disclosure metadata/access re-check behavior, bounded Sheets/XLSX reads, parser limits and provenance. Unit/integration tests cover revoke/move/schema/limit failures. The remaining acceptance blocker is application-owned OAuth consent/token custody plus deployment-only alias mapping and a live E2E run with that credential.

## Public-repository privacy rule

Do not commit the private Drive folder ID, file IDs, OAuth tokens, refresh tokens, account email, or access URLs to this repository. Deployment configuration must reference allowed resources through tenant-scoped configuration/secret storage. Evidence may record redacted resource aliases and pass/fail outcomes.

## Pilot aliases

Use these non-secret aliases in code/tests/docs:

- `pilot-drive-folder`
- `pilot-finance-sheet`
- `pilot-finance-xlsx`

The runtime maps aliases to tenant-approved source IDs outside version control.

## F3 read flow

```text
trusted tenant/actor context
  -> policy check
  -> connector instance
  -> alias -> allowlisted Drive resource ID
  -> Google metadata/access re-check
  -> expected parent + MIME check
  -> Sheets configured range OR bounded XLSX download/parse
  -> typed normalized rows
  -> source provenance + audit at orchestration boundary
  -> response/report
```

Documents, cells, formulas and file text remain untrusted data. They cannot register tools, alter access policy, choose arbitrary files, or provide executable instructions to the core.
