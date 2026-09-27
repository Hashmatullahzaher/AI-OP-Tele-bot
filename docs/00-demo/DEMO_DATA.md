# Synthetic data contract

All data in `data/fixture.json` is synthetic and unrelated to any identifiable company/customer. The name `demo-company` is a display/demo tenant, **not** proof of tenant isolation. No passwords, API tokens or external data are included.

- Simulated Google Drive spreadsheet `Sales_September_2026.xlsx`: amounts AFN 12,500 + 8,400 + 16,000 + 7,100 = 44,000; 4 rows.
- Simulated Google Drive spreadsheet `Expenses_September_2026.xlsx`: amounts AFN 4,300 + 6,100 + 2,200 = 12,600; 3 rows.
- Simulated client API `/projects`: sample budget AFN 800,000 + 450,000 = 1,250,000; sample spent AFN 315,000 + 172,500 = 487,500.
- Simulated client API `/accounts`: two illustrative account rows only; **not** balanced journal entries, complete general ledger, or a trial balance.

Restarting the demo resets the in-memory request history; fixture files are immutable during a demo. CSV files are downloaded by read-only report endpoints.
