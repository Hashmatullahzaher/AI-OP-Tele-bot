# F0/F1 contract review — exact SHA and remediation

Initial reviewed object: `faf45ce316b1a6fbcdca25992c49d252259318a6`

Initial decision: **FAIL (F1 role-separation gap)**.

The acceptance contract requires tenant isolation **and operator vs tenant roles**. The reviewed SHA had tenant-scoped actors and grants, but no explicit role model separating platform operators from tenant users/admins. CI was green, but that does not satisfy the missing acceptance behavior.

## Required remediation

- add explicit `TENANT_USER`, `TENANT_ADMIN`, and `PLATFORM_OPERATOR` actor roles;
- prevent `PLATFORM_OPERATOR` from receiving or exercising tenant-data capabilities;
- prevent platform operators from reading tenant reports/cache/credential references through tenant-resource APIs;
- retain explicit-grant authorization for tenant roles;
- add negative tests proving the separation.

After remediation, rerun the full suite and GitHub CI on the exact new SHA before F1 is accepted.
