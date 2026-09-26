# Demo charter — OS AI Core

## What is being validated
OS AI Core is a **standalone reusable AI operating layer** for many different client companies. It is not a module of Al-Biruni or another particular ERP. Its connector boundary lets one customer use an existing business/finance system via an approved API, while a different customer can use documents and spreadsheets in Google Drive even without an ERP. Telegram and later web/Windows interfaces act as channels into the same core. Cloud and customer-hosted/local deployment with the same core is an owner-confirmed requirement; packaging and operating details remain open.

## Audience and decision
Owner and early potential clients. Decide whether the cross-source conversation → read → source-grounded answer → downloadable report UX communicates the right product; agree on pilot scope and validate the first proposed live Drive read-only slice before the subsequent client-API slice. This demo is not a production deployment or a financial acceptance test.

## Immutable truth from product discovery
- Generic platform, no hard-coded business dependence on Al-Biruni.
- Intended inputs: company systems through scoped APIs and spreadsheets/documents through approved Google Drive access.
- Intended channels: Telegram text and voice; a web/desktop UI may follow.
- Intended actions: read/query, analysis, generation of Excel/PDF reports and, in a later approved phase, permissioned changes to source systems.
- Intended installation: cloud or customer-hosted/local where practical, with common product capabilities.
- Product must not confuse an LLM summary with an authoritative accounting calculation.

## Demo-specific choice
Start with a local browser-based **Telegram chat simulation** and two simulated connectors. Use visibly synthetic fixture data. No actual Telegram account, Google credential, private document, LLM token, or customer system is needed.
