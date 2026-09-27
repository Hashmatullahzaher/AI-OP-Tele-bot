# Reusable typed capability contract

Each connector installs a **versioned manifest** registered by a tenant admin, not by model-authored executable code. Candidate schema (v0.1):

```json
{
  "schema_version": "0.1",
  "connector_type": "drive|client_rest",
  "capability": "sales.total.read",
  "action": "READ",
  "input_schema": {"type": "object", "properties": {"month": {"type": "string"}}, "required": ["month"], "additionalProperties": false},
  "output_schema": {"type": "object", "properties": {"amount": {"type": "string"}, "currency": {"type": "string"}, "sources": {"type": "array"}}, "required": ["amount", "currency", "sources"]},
  "required_scopes": ["sales:read"],
  "resource_allowlist_ref": "tenant-controlled-secret-or-policy-reference",
  "mutates": false,
  "timeout_seconds": 15
}
```

Real manifests require a source-specific explicit permission mapping; schema is illustrative and must be validated before execution. Invocation context includes `tenant_id`, `actor_id`, session assurance, correlation/idempotency ID, request classification and resource scope, produced server-side. Never accept these from an untrusted model tool argument. The executor validates typed input and output, runs policy checks before connector I/O, bounds rows/time/bytes, strips secrets, logs audit metadata and refuses unregistered capabilities.

LLM returns a **proposal for an allowed tool name plus structured arguments**, not SQL, file paths, arbitrary URLs, shell commands, credentials or executable code. The result includes provenance (`source_id`, resource version/modified date, sheet/range or API route + snapshot timestamp), validation warnings and the actor's permitted disclosure category. Financial aggregates are calculated by validated code or source-authoritative service, not by freeform prose.

Write capabilities (future) require a distinct manifest and dedicated domain endpoint, precondition checks, idempotency, immutable audit and step-up approval, never an automatic upgrade from READ. Google Drive files cannot be treated as a transactional double-entry journal without an owner-approved accounting domain design.
