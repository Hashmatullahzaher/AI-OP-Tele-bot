# Security threat model and non-negotiable gates

Assets: tenant/company records, Drive documents, API/bot/OAuth tokens, credentials, prompt/tool context, reports, financial ledgers, audit logs. Threats: cross-tenant leakage, compromised Telegram session, prompt injection from untrusted files, confused-deputy model, SSRF via REST mapping, token theft, replayed write/webhook, sensitive report forward, stale ACL/cached report and local endpoint exposure.

Required controls before real data: verified identity pairing with short TTL and one-time challenge; tenant + source ACL intersection; least-privilege connector token scope; resource allowlists; encrypted secrets; webhook signature/secret checks and replay controls; private chat default; no sensitive data in logs; explicit data-processing policy and retention; per-tenant caches, namespaces and storage; URL/redirect/IP egress restrictions; rate/time/size limits; audit on read, deny and mutations; revocation invalidates sessions, access and cached artifacts. Documents and tool outputs are **untrusted data**, never instructions to change tool registry or access policy.

Never allow generic LLM -> SQL/Drive arbitrary search/REST URL/shell. Never auto-post accounting changes. High-risk actions need authenticated out-of-band confirmation and source-system validation; Telegram inline buttons alone are not sufficient authorization for financial posting. Enforce server-side checks at the exact resource fetch and report download, not only at initial chat login.

This security plan is **not implemented by the browser demo**. No real account or sensitive customer record may enter the demo.
