# AI agent architecture — source-grounded, provider-neutral

## Governing rule

The model is a **planner**, not an execution authority and not the authoritative calculator.

```text
user message
  -> model provider proposes one strict plan
  -> server validates plan against read-only tool catalog
  -> server validates typed arguments
  -> server authorizes trusted tenant/actor context
  -> typed connector executes
  -> source provenance returned
  -> deterministic server-side filter/count/sum
  -> durable redacted audit
  -> grounded response
```

The provider never creates `ExecutionContext`, tenant ID, actor ID, source authorization or capability registration. Tool/source output is not recursively fed back to the model in the F5 foundation, so instructions embedded in files/API fields cannot cause a second tool call.

## Provider abstraction

`ModelProvider.plan(...)` receives:

- user message;
- read-only typed tool catalog;
- correlation ID.

It returns exactly one schema-versioned tool plan: registered capability, typed arguments and a bounded deterministic analysis instruction (`table`, `count`, or `sum`). Unsupported/unknown fields fail closed.

The provider is deliberately not selected in source code. O-04 governs the live provider/model, privacy/data-residency and cost decision.

## Financial integrity

Financial totals are calculated by the server with `Decimal`, never by free-form model arithmetic. A sum may optionally declare a currency column. If matching rows contain multiple currencies, the request fails as ambiguous instead of adding them together.

## Source grounding

Every successful answer must include at least one `SourceProvenance` object. The authoritative response keeps structured facts separate from future optional prose rendering. If the source is unavailable or access is denied, the agent returns a stable error and never asks the model to invent a substitute answer.

## Audit

A durable `AuditSink` is mandatory. Successful source disclosure records capability, server-trusted resource scope, result and only a digestable metadata payload—not raw source records.

## Current limitations

- exactly one tool call per user request;
- no live model adapter until O-04 is resolved;
- no free-form narrative renderer yet;
- no write capability;
- no conversational memory of customer data;
- no autonomous recursion.

These are deliberate safety bounds for the first production-grade AI orchestration slice.
