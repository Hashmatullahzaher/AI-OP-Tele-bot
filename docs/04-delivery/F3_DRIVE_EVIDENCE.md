# F3 Google Drive read-only — builder evidence

Status: **IMPLEMENTED TO CREDENTIAL BOUNDARY; READY FOR INDEPENDENT AUDIT; LIVE APP-OAUTH E2E BLOCKED BY O-02**.

Branch: `app-maker/f3-drive-readonly`
Foundation remediation SHA: `b61d1e75a2c830bfaca4b0277c29d6bfa3006a35`
Verified F3 implementation SHA: `a1ba994ecd49cbf525b9f9cd4ff1e1c4abf35e3e`

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
- OAuth access tokens are injected through a token-provider boundary and are never persisted by the connector;
- CI lints the complete `tests` tree rather than only selected files.

## Automated builder verification

Local builder run:

```text
python -m compileall -q app osai tests
python -m unittest discover -s tests -v
RESULT: 43/43 PASS
```

GitHub Actions on implementation SHA `a1ba994ecd49cbf525b9f9cd4ff1e1c4abf35e3e`:

- run `36217414644`: **PASS**;
- Python 3.11: PASS;
- Python 3.12: PASS;
- Ruff full `osai tests`: PASS;
- mypy `osai`: PASS;
- compileall: PASS;
- unit suite: PASS.

Negative tests cover raw-file-ID attempts, mismatched resource scope, parent-folder change, source ACL revocation, MIME mismatch, duplicate/blank headers, XLSX size/row limits and non-execution of formula text.

## Sanctioned live corpus validation

The owner-approved Google Drive pilot folder contains only synthetic/non-sensitive data. On 2026-09-26 the authorized Drive connection successfully re-read:

- `Transactions`: 10 rows including header;
- `Accounts`: 8 rows including header;
- `Projects`: 4 rows including header;
- `Customers`: 4 rows including header;
- the stored XLSX copy with the matching finance/operations corpus.

The local bounded XLSX parser also parsed all four worksheets with matching headers. This validates the corpus and parser compatibility. It does **not** substitute for an app-owned OAuth credential. Private Drive IDs, URLs, account email and tokens are not committed.

## Remaining exact blocker for full F3 acceptance

`O-02`: provision an OS AI Core Google OAuth client/consent configuration with least-privilege read-only scopes, choose token custody/revocation implementation, and place pilot aliases -> Drive IDs in tenant-scoped deployment configuration outside Git. Then run application-owned OAuth E2E against the sanctioned pilot account, including revoke/share-change scenarios.

## Handoff

Builder status: **READY_FOR_INDEPENDENT_AUDIT**. Audit the exact branch SHA containing this evidence against `docs/04-delivery/ACCEPTANCE_CONTRACT.md`. Full F3 acceptance remains blocked by O-02 even if the code audit passes.
