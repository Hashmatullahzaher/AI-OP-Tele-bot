# Windows PC / Windows Server — first UAT target

Owner decision: **Windows is the first real UAT host** (2026-09-26).

This runbook is for a controlled test host, not yet a production certification. Use non-sensitive pilot data and deployment secret references only.

## Supported UAT shape

- Windows 11 / Windows Server with Python 3.11+.
- OS AI Core runs the same Python core used by cloud/container builds.
- Local runtime binds to `127.0.0.1` by default.
- Internet access is required for live Telegram, Google Drive and hosted LLM providers.
- A LAN-only customer ERP may be reached locally only through an explicitly configured client connector.
- No public unauthenticated port is allowed.

## UAT preparation

1. Clone the approved exact SHA.
2. Run `scripts/windows/uat-check.ps1` from PowerShell.
3. Keep `OSAI_PROFILE=local`.
4. Store real secrets outside Git and outside the repository working tree.
5. Use a dedicated Windows service account for later production service installation; do not run as a shared administrator account.
6. Enable Windows Firewall rules explicitly only when a reviewed local integration requires them.
7. Back up the SQLite database before version changes and test restore before production use.

## Mutation UAT

The first write actions are limited to:
- customer create,
- customer update,
- procurement request create,
- draft voucher create.

Run only against a sanctioned sandbox API. Confirm the source endpoint enforces the same actor/tenant identity and idempotency key. Draft voucher creation must stay DRAFT. Posting or editing posted vouchers is out of scope.

## Not yet certified

This repository does not yet claim:
- Windows Service installation/upgrade automation,
- production secrets backend,
- production TLS/gateway configuration,
- production customer write API,
- production backup schedule/RPO/RTO,
- monitoring/alerting,
- live step-up approval mechanism.

Those are explicit release gates, not implementation assumptions.
