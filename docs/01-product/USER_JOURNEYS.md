# Critical user journeys (draft)

**J-01 ERP-less company / Google Drive:** tenant admin consents to a scoped Google connection and selects allowed test documents; employee pairs private Telegram with company identity; asks “sales total for September”; tool retrieves authorized rows, uses deterministic decimal aggregation, responds with number, currency and file name/tab/coverage. If the file is missing/stale or ambiguous, agent asks for clarification rather than inventing a total.

**J-02 Existing client system:** tenant admin registers a versioned allowlisted REST endpoint and mapping for `projects.summary`; an authorized employee asks about budget versus spending; caller identity and tenant scope are forwarded in signed service context; customer domain service performs source authorization; agent presents confirmed figures with report date. No ERP-specific table layout is embedded in core.

**J-03 Restricted information:** user asks for another employee's payroll data; server-side rights check denies the tool before data reaches the model; response contains no restricted values; denial metadata is audited.

**J-04 Proposed write (future, excluded from read-only pilot):** employee asks for a journal change; AI may prepare a typed draft but cannot post. Source-system domain service validates role, independent confirmation and double-entry invariant; posted records are corrected by reversal/adjustment, never blind update.

**J-05 Local deployment:** customer installs the same backend on an authorized server and connects it to the internet via controlled outbound network path for Telegram/Google; when disconnected, live remote channels clearly report unavailable rather than pretending synchronization.
