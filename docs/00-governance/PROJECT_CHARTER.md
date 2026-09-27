# Project charter — OS AI Core

**Mission:** Build a reusable, standalone AI operating layer for organizations with or without existing business software. An organization can connect an approved set of Google Drive files or existing REST API services, ask authorized questions through Telegram or other channels, and obtain traceable answers and reports.

**Users:** platform operator; customer tenant owner/admin; company employee; external source-system administrator; auditor/reviewer. One customer company must never inherit access to another company's data.

**Boundaries:** Shared code, provider/model abstraction, typed capability registry, connectors, channel adapters, identity, audit and reporting belong to OS AI Core. Customer-specific accounting rules and authoritative writes remain in each source system/domain service. Google Drive is not treated as an accounting ledger.

**Deployment:** Same core application runs in cloud or customer-hosted environment with separately configured secrets and connectors. Windows application is an optional future control-panel packaging choice, not a second backend. A fully disconnected local host cannot use live Telegram or Google Drive until it has controlled internet connectivity.

**First live pilot (proposed):** one authorized organization, private Telegram chat only, read-only Drive and XLSX/Sheets retrieval, source-grounded questions, audit, export; client REST API read-only is a subsequent connector milestone. No client records or external credentials are in this repository or demo.

**Out of scope until later contracts:** real-time Telegram voice calling, arbitrary database access, autonomous financial posting, cross-company pooled search, editing unapproved Drive files, automatic source-system installation, and presenting model-generated amounts as authoritative.

**Success:** a second organization can provision the same core without code changes to orchestration; connector mapping is configured, permitted data stays inside its tenant, operations are demonstrable via real e2e tests and reversible deployments.
