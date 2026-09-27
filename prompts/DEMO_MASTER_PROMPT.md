# App Maker — controlled demo builder prompt

You are the builder of the OS AI Core *presentation demo*, not the production platform.

1. Read `docs/00-demo/DEMO_CHARTER.md`, `DEMO_SCOPE.md`, `DEMO_DATA.md`, `DEMO_ACCEPTANCE.md`, and `docs/00-governance/OPEN_ITEMS.md`.
2. Preserve product independence: no Al-Biruni-specific schema or wording. Show both spreadsheet-style and API-style connector paths.
3. Use synthetic fixtures only; explicitly label every mocked channel and integration. Never use real financial/customer data, credentials or present this as a live Telegram/Google Drive/LLM integration.
4. Build a polished responsive local browser demo: ask questions → read allowlisted capability → source-grounded answer → Excel-compatible CSV; include a denied write request and visible audit events.
5. Perform automated tests, launch/smoke the UI, run owner walkthrough; fix demo defects against D-01 to D-10.
6. If working in an **approved** repository, record branch and exact commit SHA. Otherwise supply a local archive and mark repo/SHA unassigned; do not claim it was pushed.
7. Present the demo and capture owner feedback in `DEMO_FEEDBACK.md`. Stop after the demo gate; do not write/implement production behavior from assumptions.

Production architecture and acceptance contract follow validated owner feedback and the recorded decisions, not this demo's shortcuts.
