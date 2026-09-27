# Windows Installer UAT

Status: **UAT packaging only — not production certified**.

This stage turns the proven Windows-local Core into a user-installable package. It does not change F9 authorization,
financial invariants, Telegram approval rules, or the Excel sandbox mutation contract.

## User experience

1. Run `OS-AI-Core-Setup.exe` as an administrator.
2. The installer places the standalone runtime under `Program Files\OS AI Core`.
3. It creates an automatic Windows service named `OSAICore` with display name `OS AI Core`.
4. The service runs as `NT AUTHORITY\LocalService`, not as a shared administrator account.
5. Runtime data is stored under `ProgramData\OS AI Core` and survives uninstall for recovery.
6. A Start Menu shortcut opens `http://127.0.0.1:8765/` in the default browser.
7. The page is the real local operator dashboard served by `osai.runtime`; it is not the legacy demo UI.

## Security defaults

- local profile only;
- loopback binding (`127.0.0.1:8765`);
- offline dependency mode by default;
- no inbound firewall rule is created;
- no Telegram token, LLM API key, Drive credential, or customer credential is embedded in the installer;
- ProgramData ACL is restricted to Administrators, SYSTEM, and LocalService;
- the operator dashboard exposes health/readiness only and applies no-store, CSP, frame-deny, and no-referrer headers.

## CI acceptance

The Windows installer workflow must:

- build the service executable with PyInstaller;
- compile the installer with Inno Setup;
- silently install the package on a clean Windows runner;
- verify the `OSAICore` service is Automatic and runs as LocalService;
- verify `/healthz`, `/readyz`, and the operator dashboard over loopback;
- verify the SQLite database is created under ProgramData;
- uninstall the package and verify the Windows service is removed;
- preserve ProgramData after uninstall;
- upload the generated installer as a short-lived CI artifact.

## Explicit remaining release gates

The UAT installer is not a production release until all applicable gates are satisfied:

- Authenticode code signing and release provenance;
- production secrets provisioning/rotation;
- reviewed upgrade/rollback behavior against existing live state;
- monitoring/alerting and backup schedule/RPO/RTO;
- live Telegram and LLM provider configuration;
- production customer API integration;
- independent audit of the exact release SHA and installer hash.

Windows SmartScreen may warn on the UAT installer because it is intentionally unsigned at this stage.
