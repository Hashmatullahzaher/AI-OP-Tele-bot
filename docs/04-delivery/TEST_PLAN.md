# Verification strategy

Demo: `python -m unittest discover -s tests -v`, local HTTP health/chat/report smoke checks, visual owner walkthrough, checks D-01 to D-10. No real auth claims.

Production unit: typed schema reject extra keys, decimal accuracy, policy denial, manifest signature/registration, source coverage, output format/units, source timestamp policy. Integration: Drive allowlist and ACL changes, OAuth expiry/revoke, Sheets/XLSX format errors, REST endpoint/hostname allowlist, source-side auth, tenant DB isolation, persistence of audit/artifact expiry. E2E: paired private Telegram user -> tenant permission -> selected source -> grounded answer -> limited report download; replay/compromised/revoked user denied; local and cloud profiles run same tests. Adversarial: spreadsheet prompt injection, malformed Excel formula, SSRF/redirect, cross-tenant leak, excessive file/row size, bot webhook spoof, repeated approval, stale cached doc, provider hallucination/outage.

Before acceptance, record test command, environment, exact commit SHA, pass/fail counts, source test account classification, known limitations and independent auditor decision. No simulated tests are counted as proof of live Google/Telegram integration.
