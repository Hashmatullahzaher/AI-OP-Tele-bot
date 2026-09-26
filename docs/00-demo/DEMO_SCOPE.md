# Demo scope and boundaries

## Included
1. Responsive Dari presentation UI showing the universal OS AI product, explicit "DEMO" markers and modular connector cards.
2. Telegram-style *browser simulation*: typed questions and grounded replies, not a live bot.
3. Two interchangeable *simulated* data sources: spreadsheet-style sales/expenses and REST-API-style projects/accounts.
4. Small allowlisted deterministic router from recognized question types to a typed demo capability. `Decimal` computes numbers from fixture records; no free-form SQL or arbitrary code execution.
5. Source name, row count, capability and CSV export for supported query types; UTF-8 CSV opens in Excel but is NOT native XLSX.
6. Simulated write protection; audit events kept in memory for the current server lifetime.

## Excluded and not falsely implied
- Live Telegram Bot API, Telegram voice calls or voice messages, Google OAuth/Drive/Sheets, real ERP APIs, LLM inference, real identity/permissions/multi-tenant security, email or scheduled reports, XLSX/PDF generation, durable logs, production accounting writes, deployment packaging.
- Financial records shown are isolated sample records; account listing does not purport to be a balanced general ledger.
- The browser has no authentication and may not be exposed to the internet or real client data.

## Demo journey
Ask September sales → inspect answer/synthetic source → download CSV → ask construction project budget and spent → inspect API-style source → request write → see denial and audit entry.
