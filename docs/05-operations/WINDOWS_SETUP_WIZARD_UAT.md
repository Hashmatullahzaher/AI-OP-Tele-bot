# Windows Setup Wizard UAT

Status: **UAT CONFIGURATION LAYER — LIVE TELEGRAM/LLM CONNECTORS STILL GATED**

This stage extends the installable Windows package with a loopback-only Setup Wizard at:

`http://127.0.0.1:8765/setup`

## UAT capabilities

The wizard can:

- create and activate the managed Excel Sandbox workbook under ProgramData;
- persist non-secret local configuration;
- accept a Telegram bot alias and bot token;
- select OpenAI or a loopback-only local model provider;
- store an OpenAI API key when OpenAI is selected;
- clear stored Telegram/OpenAI credentials;
- report only whether credentials are configured.

The wizard does **not** yet activate live Telegram or LLM network traffic. The UI and API explicitly report those connectors as pending.

## Secret handling

On packaged Windows installs:

- secrets are handled by the OS AI Core Windows service;
- secret plaintext is never written to `settings.json`;
- secrets are protected with Windows DPAPI using UI-forbidden + local-machine protection;
- the encrypted DPAPI payload is stored under `ProgramData\OS AI Core\config`;
- ProgramData ACLs restrict the tree to SYSTEM, Administrators and LocalService;
- setup/status APIs return booleans only, never secret values.

The Windows installer smoke test writes synthetic Telegram/OpenAI credentials, verifies they are not present in plaintext in either settings or secret files, and confirms status without exposing the values.

## Web security

Setup is local-profile only. Mutation requests require all of:

- loopback Host header;
- loopback same-origin Origin header;
- an in-memory high-entropy CSRF token embedded in the locally served page;
- application/json request body;
- bounded request size;
- strict typed field validation.

No CORS relaxation is added.

## Excel safety

The Create/Use Managed Workbook action creates the controlled UAT workbook only when it does not already exist. It does not overwrite an existing workbook.

## Explicit non-claims

This stage does not certify:

- live Telegram connectivity;
- live OpenAI or local-model inference;
- Google Drive OAuth;
- production customer API connectivity;
- Authenticode signing;
- production credential rotation/recovery procedures;
- remote/LAN dashboard exposure.

Those remain separate release/integration gates.
