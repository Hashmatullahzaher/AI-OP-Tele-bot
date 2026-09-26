# F0/F1 builder evidence — foundation slice

Status: **IMPLEMENTED ON FEATURE BRANCH; LOCAL TESTS PASS; CI/INDEPENDENT AUDIT PENDING**.

This evidence describes the production-foundation package under `osai/`. The presentation demo under `app/` remains separate and is not treated as production evidence.

## F0 — shared foundation

Implemented:

- strict `CapabilityManifest` contract with unknown-field rejection;
- small JSON-schema subset with `additionalProperties=false` enforcement;
- explicit capability allowlist/registry;
- execution boundary `validate input -> server policy -> connector -> validate output`;
- server-issued `ExecutionContext` carrying tenant/actor/correlation/resource scope;
- typed source provenance;
- strict local/cloud runtime configuration; local profile rejects public bind;
- no arbitrary SQL, URL, shell, credential, or file-path execution route;
- CI definition for Python 3.11/3.12, Ruff, mypy, compile and tests.

Objective local verification:

```text
python -m compileall -q app osai
python -m unittest discover -s tests -v
RESULT: 26/26 PASS
```

The environment used for this builder run could not download Ruff/mypy packages because outbound package resolution was unavailable. Therefore lint/typecheck are configured for CI but **not claimed locally**. F0 is not marked fully accepted until CI and independent audit verify the exact commit SHA.

## F1 — tenant identity/policy/audit foundation

Implemented:

- SQLite tenant and actor records with compound tenant ownership;
- active/revoked actor gate;
- capability/resource grants;
- tenant-scoped cache/report object reads;
- connector records storing only opaque `secretref:` references;
- cross-tenant report/cache/credential lookup denial;
- persistent audit metadata with sensitive-payload digest rather than raw payload;
- per-tenant hash-chain verification for tamper evidence;
- audit durability verified across database close/reopen.

This is an identity/policy persistence **foundation**, not a complete production identity provider, SSO system, secrets manager, or migration/backup solution. F1 remains subject to CI, independent audit, and later production storage/secrets decisions.

## Drive test-corpus readiness

The owner-controlled synthetic Google Drive pilot corpus was provisioned and manually validated through the authorized connector environment. Native Google Sheet and XLSX reads both succeeded. Private Drive IDs and credentials are intentionally absent from this public repository. See `docs/03-architecture/DRIVE_PILOT.md`.
