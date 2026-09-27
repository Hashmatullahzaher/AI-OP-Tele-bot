# Windows PC / Windows Server — first UAT target

Owner decision: **Windows is the first real UAT host** (2026-09-26).

This runbook is for a controlled test host, not yet a production certification. Use non-sensitive pilot data and deployment secret references only.

## Supported UAT shape

- Windows 11 / Windows Server.
- OS AI Core runs the same Python core used by cloud/container builds.
- Source-level UAT requires Python 3.11+; the packaged installer bundles the runtime and does not require the end user to install Python.
- Local runtime binds to `127.0.0.1` by default.
- Internet access is required only after live Telegram, Google Drive, hosted LLM, or remote customer API capabilities are explicitly enabled.
- A LAN-only customer ERP may be reached locally only through an explicitly configured client connector.
- No public unauthenticated port is allowed.

## Source UAT preparation

1. Clone the approved exact SHA.
2. Run `scripts/windows/uat-check.ps1` from PowerShell.
3. Keep `OSAI_PROFILE=local`.
4. Store real secrets outside Git and outside the repository working tree.
5. Enable Windows Firewall rules explicitly only when a reviewed local integration requires them.
6. Back up the SQLite database before version changes and test restore before production use.

## Installer UAT

The packaging stage adds `OS-AI-Core-Setup.exe`. It installs `OSAICore` as an Automatic Windows service under the
low-privilege `NT AUTHORITY\LocalService` account and opens the local operator dashboard through a Start Menu shortcut.
See `WINDOWS_INSTALLER_UAT.md` for the exact acceptance contract and remaining release gates.

## Mutation UAT

The first write actions are limited to:
- customer create,
- customer update,
- procurement request create,
- draft voucher create.

Run only against a sanctioned sandbox API or the approved Excel sandbox. Confirm the source endpoint enforces the same
actor/tenant identity and idempotency key. Draft voucher creation must stay DRAFT. Posting or editing posted vouchers is out of scope.

## Not yet production certified

This repository does not yet claim:
- signed production Windows release artifacts,
- production secrets backend/provisioning,
- production TLS/gateway configuration,
- production customer write API,
- production backup schedule/RPO/RTO,
- monitoring/alerting,
- live external-provider credentials on the installer host.

Those are explicit release gates, not implementation assumptions.
