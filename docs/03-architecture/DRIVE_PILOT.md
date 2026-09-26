# Google Drive read-only pilot — sanctioned test corpus

Status: **OWNER-APPROVED TEST SOURCE; APP OAUTH NOT YET IMPLEMENTED** (2026-09-26).

The owner selected an owner-controlled Google Drive folder for the first real-source pilot. The folder contains only synthetic/non-sensitive test data and currently includes:

- one native Google Sheet named `OS AI Core Pilot - Finance & Operations`;
- one XLSX export of the same corpus named `OS AI Core Pilot - Finance & Operations.xlsx`;
- tabs/data domains for `Transactions`, `Accounts`, `Projects`, and `Customers`;
- AFN-only synthetic financial figures suitable for deterministic read tests.

A connector-side validation outside the application confirmed that the authorized Google account can list the pilot folder, read the native `Projects` range, and extract rows from the XLSX file. This proves the **test corpus is usable**, not that OS AI Core F3 is complete. The application still needs its own approved OAuth client/scopes, token custody, resource allowlist configuration, ACL re-check logic, parser limits, provenance capture, and revocation tests.

## Public-repository privacy rule

Do not commit the private Drive folder ID, file IDs, OAuth tokens, refresh tokens, account email, or access URLs to this repository. Deployment configuration must reference allowed resources through tenant-scoped configuration/secret storage. Evidence may record redacted resource aliases and pass/fail outcomes.

## Pilot aliases

Use these non-secret aliases in code/tests/docs:

- `pilot-drive-folder`
- `pilot-finance-sheet`
- `pilot-finance-xlsx`

The runtime maps aliases to tenant-approved source IDs outside version control.

## F3 intended read flow

```text
trusted tenant/actor context
  -> policy check
  -> connector instance
  -> alias -> allowlisted Drive resource ID
  -> Google ACL/resource re-check
  -> Sheets API range OR bounded XLSX download/parse
  -> typed normalized rows
  -> deterministic calculation
  -> source provenance + audit
  -> response/report
```

Documents, cells, formulas and file text remain untrusted data. They cannot register tools, alter access policy, choose arbitrary files, or provide executable instructions to the core.
