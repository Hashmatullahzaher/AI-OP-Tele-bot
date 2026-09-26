# F6 Reports — builder evidence

Status: **IMPLEMENTED FOR DETERMINISTIC SOURCE-LINKED ARTIFACTS; PILOT FORMAT/RETENTION APPROVAL O-09 REMAINS OPEN**.

Base SHA: `acf2768bf5b42d54c2b8b60c889da83348f1d34e`
Branch: `app-maker/f6-reports`

## Implemented

- deterministic report creation from already-authorized normalized source rows; no LLM financial arithmetic;
- mandatory source provenance with source ID/type/revision/locator;
- CSV with UTF-8 BOM and exact row coverage;
- native XLSX OOXML artifact with Unicode-safe cell content;
- native PDF baseline with source revision, period, row count and total; non-Latin PDF fails closed until a Unicode font policy is configured;
- Decimal total calculation and single-currency validation when financial columns are declared;
- exact deterministic filenames from report title and period;
- opaque high-entropy report IDs;
- tenant/actor ownership checked again at every download;
- revoked actor and cross-tenant/cross-actor download denied;
- bounded TTL with expiry checked at download;
- SHA-256 and size integrity verification before disclosure;
- filesystem artifacts written atomically with owner-only file mode;
- create/download events added to the existing redacted tenant audit chain.

## Builder verification

Local focused suite:

```text
python -m compileall -q osai tests
python -m unittest tests.test_reports -v
RESULT: 11/11 PASS
```

The full GitHub CI suite must pass on the exact branch SHA before handoff.

## Acceptance coverage

F6 contract checks covered by automated tests:

- unauthorized report download fails;
- expired report link fails;
- revoked actor fails;
- artifact tampering fails integrity verification;
- exact filename asserted;
- CSV/XLSX/PDF native signatures/contents asserted;
- row coverage asserted;
- source revision asserted;
- deterministic money and currency asserted;
- mixed currency / invalid numeric data fail closed.

## Remaining pilot input

`O-09` still controls production-format acceptance: approved first-pilot XLSX/PDF layout, Unicode/Dari PDF font packaging/licensing, report retention/delivery policy, and voice provider choices. F6 does not fabricate those presentation rules. The current PDF renderer deliberately rejects non-Latin text rather than corrupting it.
