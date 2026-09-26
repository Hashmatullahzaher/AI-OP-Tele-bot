# Dual-deployment runbook — architecture target, not production instructions

**Today:** demo must bind to loopback only; run `python -m app.server` and browse `http://127.0.0.1:8765`. No public deployment of demo.

**Future cloud:** deploy same signed core image behind authenticated HTTPS gateway/webhook, isolated tenant storage, per-tenant secret references, managed backup/restore, audit storage, health checks and restricted outbound egress. Never bake bot/OAuth/API secrets into images.

**Future customer-hosted/local:** same signed core image on approved host/container with local secrets/database/backup and loopback admin interface. Provide controlled outbound connectivity to Telegram/Google; when LAN-only ERP requires remote cloud orchestration, use mutual-authenticated outbound connector relay after owner threat review. Air-gapped mode cannot provide live Telegram or Google Drive. Test upgrades, rollbacks and backup restores independently in both profiles.

**Security gate:** no external credentials in repository, no production customer data in demo, no public HTTP port by default, least-privilege network egress and logs redacted. Values for OS/hardware/host provider, bot mode and backup retention are open items and cannot be guessed.
