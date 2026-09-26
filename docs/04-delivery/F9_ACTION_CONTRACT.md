# F9 permissioned AI actions — pilot contract

Status: **OWNER-SCOPED 2026-09-26; GENERIC BUILDER IMPLEMENTATION, LIVE CUSTOMER API E2E STILL GATED**

The owner selected Windows PC / Windows Server as the first UAT deployment target and approved these first mutation classes:

1. `customer.create`
2. `customer.update`
3. `procurement.request.create`
4. `finance.draft_voucher.create`

No other mutation is implied. In particular, voucher posting, deletion, posted-entry editing, arbitrary SQL, arbitrary REST URLs and general database writes remain prohibited.

## Shared mutation gate

Every action must pass, in order:

```text
trusted actor/tenant context
-> active actor + explicit capability grant
-> strict typed payload validation
-> deterministic domain validation
-> step-up approval bound to action + payload digest
-> tenant-scoped idempotency reservation
-> registered source-domain adapter
-> source-system actor/tenant authorization
-> source-system idempotency confirmation
-> allowed source state validation
-> durable audit + receipt
```

The AI/model may propose an action and payload, but it never supplies trusted tenant identity, source credentials, approval truth, arbitrary endpoint details or bypasses server-side policy.

## Pilot action contracts

### customer.create
Required: name, customer type. Optional contact/address fields. Source must create a new customer record and return the same authorized tenant and actor in the receipt.

### customer.update
Required: immutable customer identifier plus at least one allowed change. The core permits only name, phone, email, address and ACTIVE/INACTIVE status in the generic pilot contract. Customer identity cannot be changed.

### procurement.request.create
Required: requester, department, currency and at least one line item. Quantity must be positive; estimated unit cost cannot be negative. The core derives the estimated total using Decimal arithmetic before the source call. Approval/workflow status after creation remains source-system policy.

### finance.draft_voucher.create
This action creates a **DRAFT only**. It requires at least two entries, fixed-precision debit/credit values and a balanced journal where total debit equals total credit and is greater than zero. A line cannot carry both debit and credit.

The generic F9 layer has no capability to post the voucher. A source response other than DRAFT is rejected. Posted records remain immutable; future corrections must use an explicitly designed reversal/correction workflow.

## Approval and idempotency

- Every mutation requires a short-lived step-up approval reference checked outside model-controlled content.
- Approval is bound to the action and SHA-256 digest of the normalized payload.
- Every mutation requires an idempotency key.
- Replaying the exact same completed request returns the prior receipt without a second source call.
- Reusing an idempotency key for a different action/payload/actor is rejected.
- If the local journal is IN_PROGRESS after an uncertain source outcome, automatic retry is denied pending reconciliation.
- The customer source endpoint must independently honor the same idempotency key.

## Source integration

A client adapter may call only pre-registered HTTPS mutation routes. Method, host, path, allowed fields and receipt mapping are deployment configuration, not model input. Redirects, localhost/literal-IP targets and caller-supplied URLs are prohibited.

The source response must confirm:
- tenant ID,
- authorized actor ID,
- idempotency key,
- source record ID,
- allowed state,
- optional revision/version.

## Windows-first UAT

The first operational UAT target is Windows PC / Windows Server. This resolves the first-host choice only; production Windows service packaging, secrets manager, TLS/gateway strategy, backup schedule and customer-specific source API details remain acceptance inputs.

## Exact remaining blockers for live F9 acceptance

- sanctioned customer write API/OpenAPI contract for the four actions;
- source-side actor/tenant authorization semantics;
- source-side idempotency semantics;
- production step-up approval mechanism and expiry rules;
- exact customer/procurement field rules and approval limits;
- Windows production secret storage/service-account policy.

Generic typed implementation and adversarial tests may proceed without inventing those client-specific rules.
