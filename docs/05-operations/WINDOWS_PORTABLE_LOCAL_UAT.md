# Windows Portable Local UAT

Status: **UAT portable packaging — not production certified**

The owner requested a second local Windows path that does not depend on the Windows installer or the OSAICore service.

## User experience

- download one file: OS-AI-Core-Portable.exe;
- double-click it — no installation is required;
- no Windows Service is created;
- no Administrator elevation is required;
- a small control window remains open while OS AI Core is running;
- buttons open the Dashboard, Setup Wizard, and per-user data folder;
- closing the control window stops the local Core.

## Isolation

Portable mode intentionally uses:

- loopback only: 127.0.0.1;
- port 8766, so it can coexist with the installed Windows Service UAT on port 8765;
- per-user data root: %LOCALAPPDATA%\OS AI Core Portable;
- offline dependency mode by default;
- the same runtime, setup validation, Excel sandbox, and secret-store contracts as the installed local mode.

## Security boundary

- no inbound firewall rule;
- no embedded Telegram token, LLM API key, Drive credential, or customer credential;
- Setup access still uses a high-entropy local access token plus CSRF/same-origin checks;
- secrets are never returned by Setup status APIs;
- Windows DPAPI protects configured secret values in UAT;
- portable data is scoped under the signed-in user's LocalAppData instead of ProgramData.

## CI acceptance

Windows CI must:

1. run portable/setup contract tests;
2. build the one-file executable with PyInstaller;
3. execute the bundled check command;
4. start the bundled executable in headless serve mode;
5. verify /healthz, the real operator Dashboard, and Setup Wizard on port 8766;
6. verify the SQLite database is created under LocalAppData;
7. upload the portable EXE as a short-lived artifact.

## Explicit non-claims

This is not yet a signed production desktop application. Live Telegram and live LLM activation remain separate acceptance gates. The Portable control window is a local UAT launcher, not a full desktop client UI.