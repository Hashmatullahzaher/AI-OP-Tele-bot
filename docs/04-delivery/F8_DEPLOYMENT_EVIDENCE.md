# F8 dual deployment / operations — builder evidence

Status: **GENERIC PACKAGING/OPERATIONS IMPLEMENTED; ENVIRONMENT CERTIFICATION BLOCKED BY O-08**.

Base: F7 exact SHA `9a97b3fb128c691d9b5fde886c3b070146671cbe`.

Implemented:

- one Python core/runtime for both local and cloud profiles;
- one OCI/Docker image for both profiles;
- local reference deployment binds the health/runtime surface to loopback using host networking, with no published public port;
- cloud reference deployment exposes only an internal container port and requires an HTTPS `OSAI_PUBLIC_BASE_URL` supplied by the deployment environment/gateway;
- `/healthz` and `/readyz` expose no tenant data;
- deployment readiness fails closed for required Telegram/Drive/client-API/LLM dependencies when the runtime is offline, unconfigured, or marked unhealthy;
- SQLite snapshot uses the SQLite backup API, integrity check, atomic replacement, SHA-256 metadata and 0600 permissions where supported;
- restore verifies checksum and SQLite integrity before atomic replacement;
- container CI builds the same image and executes local/cloud configuration smoke checks with network disabled.

Security boundary:

This milestone does **not** select a cloud vendor, Windows/Linux target, secrets manager, TLS gateway, monitoring stack, storage class, backup schedule, RPO/RTO, or local-network bridge. Those are O-08 inputs and are required for final F8 certification. The local Compose file is a Linux host-network reference; a Windows service/Docker Desktop package must be finalized only after the target environment is selected.

No Telegram/Drive functionality is reported as available merely because the process is alive. External readiness is separate and fail-closed.
