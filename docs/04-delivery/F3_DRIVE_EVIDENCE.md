# F3 Google Drive read-only — builder evidence

Status: **IMPLEMENTED TO CREDENTIAL BOUNDARY; LIVE APP-OAUTH E2E BLOCKED ONLY BY O-02**.

Branch: `app-maker/f3-drive-readonly`
Base SHA: `faf45ce316b1a6fbcdca25992c49d252259318a6`

## Implemented

- real Google Drive v3 / Google Sheets v4 HTTP adapter using HTTPS only;
- no Drive search capability and no caller/model-supplied raw Drive IDs, URLs or arbitrary A1 ranges;
- server-configured resource aliases and table aliases;
- exact MIME and expected-parent-folder enforcement;
- Drive metadata is re-fetched on every disclosure so revoked/unshared/moved sources fail closed;
- actor execution scope must match the requested resource alias;
- Sheets values are read only through configured ranges;
- XLSX files are downloaded with byte limits and parsed with archive/XML, row and column limits;
- XLSX formulas/macros are never executed; only cached cell values are read;
- duplicate/blank headers fail as `DriveSchemaAmbiguous` instead of allowing guessed calculations;
- source revision, source type, sheet/table locator and alias are returned as provenance;
- Google 401/403/404 become non-enumerating access denials; 429/5xx become source-unavailable failures;
- OAuth access tokens are injected through a token-provider boundary and are never persisted by the connector.

## Automated builder verification

```text
python -m compileall -q osai tests
python -m unittest discover -s tests -v
RESULT: 41/41 PASS
```

New negative tests cover raw-file-ID attempts, mismatched resource scope, parent-folder change, source ACL revocation, MIME mismatch, duplicate/blank headers, XLSX size/row limits and non-execution of formula text.

Ruff/mypy are intentionally not claimed locally in this environment because those executables are unavailable. GitHub CI must verify the branch exact SHA.

## Sanctioned live corpus validation

The owner-approved Google Drive pilot folder remains synthetic/non-sensitive. Through the authorized Google Drive connection, the builder re-read all four native Sheet tabs (`Transactions`, `Accounts`, `Projects`, `Customers`) and the stored XLSX copy on 2026-09-26. Both file types were readable and the local bounded XLSX parser successfully parsed all four sheets from the exported pilot XLSX.

This validates the corpus and parser compatibility. It does **not** substitute for an app-owned OAuth credential. No private folder/file IDs, URLs, account email or tokens are committed.

## Remaining exact blocker for full F3 acceptance

`O-02`: provision an OS AI Core Google OAuth client/consent configuration with read-only scopes, choose token custody/revocation implementation, and place the pilot aliases -> Drive IDs in tenant-scoped deployment configuration outside Git. Then run the same F3 tests against the application connector using the sanctioned pilot account and revoke/share-change scenarios.
