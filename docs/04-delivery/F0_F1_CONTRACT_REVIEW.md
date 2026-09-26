# F0/F1 contract review and remediation

Initial reviewed object: `faf45ce316b1a6fbcdca25992c49d252259318a6`

Initial decision: **FAIL (F1 role-separation gap)**. The acceptance contract requires tenant isolation **and operator vs tenant roles**; the initial SHA had no explicit role model.

Remediation object: `b61d1e75a2c830bfaca4b0277c29d6bfa3006a35`

Remediation implemented:

- explicit `TENANT_USER`, `TENANT_ADMIN`, and `PLATFORM_OPERATOR` roles;
- platform operators cannot receive or exercise tenant-data capabilities;
- platform operators cannot read tenant report/cache/credential references through tenant-resource APIs;
- tenant roles retain explicit-grant authorization;
- negative tests cover operator/tenant separation.

Verification:

- GitHub Actions run `36217089400`: **PASS**;
- Python 3.11: PASS; Python 3.12: PASS;
- Ruff: PASS; mypy: PASS; compileall: PASS; unit suite: PASS.

Development gate decision: **PASS for proceeding to the next milestone**. A separately instantiated independent auditor remains required before production certification/merge under the App Maker contract.
