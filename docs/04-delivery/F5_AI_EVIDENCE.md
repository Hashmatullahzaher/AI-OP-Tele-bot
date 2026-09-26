# F5 source-grounded AI — builder evidence

Status: **IMPLEMENTED TO PROVIDER-ADAPTER BOUNDARY; LIVE MODEL RUN BLOCKED BY O-04**.

Base SHA: `da4bccf5519386eb68b33215e09fba87131e9720` (F4 builder handoff).

## Implemented

- provider-neutral `ModelProvider` planning interface;
- model receives no trusted tenant/actor authorization context;
- only registered **READ** capabilities are exposed to the planner catalog;
- exactly one tool call per request;
- strict plan shape and typed tool-argument validation before execution;
- mandatory server-side policy check before source I/O;
- existing connector/tool schema boundary remains authoritative;
- source/tool output is never recursively sent back to the planner;
- successful response requires source provenance;
- source outage/access/schema/provider failures have explicit stable failure codes;
- deterministic filtering/counting/summing after source retrieval;
- `Decimal` financial arithmetic;
- mixed-currency sums fail closed;
- mandatory durable audit sink before a grounded answer is disclosed.

## Adversarial tests

`tests/test_agent.py` verifies:

- an injected request to call `admin.sql.run` cannot select an unregistered capability;
- write capabilities are absent from the planner catalog;
- unknown SQL/tool arguments fail before connector execution;
- source content containing prompt-injection text cannot trigger recursive tool calls;
- source outage does not cause a fabricated fallback;
- provider outage does not call tools;
- mixed currencies and non-numeric financial values fail closed;
- financial sums and filters are deterministic.

CI evidence is pending until the branch exact SHA runs.

## Exact remaining blocker for full F5 acceptance

`O-04`: choose one sanctioned live LLM provider/model and the per-tenant privacy/data-residency/cost policy, then implement and test that adapter against the same strict plan schema. The core does not require OpenAI specifically; local or other hosted providers may implement the same contract.
