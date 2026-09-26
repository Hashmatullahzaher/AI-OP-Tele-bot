# F2 Telegram private text — builder evidence

Status: **IMPLEMENTED TO SECRET/NETWORK BOUNDARY; LIVE SANDBOX BOT E2E BLOCKED BY O-05**.

Base SHA: `5072230ce18ba135ad9a0235753ea79a484d03fb` (F5 builder handoff).

## Implemented

- runtime webhook-secret provider; constant-time secret comparison;
- authenticated update ID persistence and replay denial;
- private-chat/text-only ingress;
- high-entropy, TTL-bound, single-use out-of-band pairing;
- only pairing-code hash is stored;
- persistent bot/chat/user -> tenant/actor binding;
- binding and actor revocation;
- server-issued trusted `ExecutionContext`;
- redacted channel security events;
- tenant audit after identity is known without storing message text;
- outbound `sendMessage` boundary with runtime bot-token provider;
- outbound active-binding and tenant/actor re-check before token use;
- Telegram-only HTTPS transport and redirects disabled.

## Automated test intent

`tests/test_telegram.py` covers pairing and route, hash-only pairing storage, expiry, invalid webhook, replay, group/non-text denial, unpaired denial, binding/actor revocation, duplicate JSON fields, redacted logs, outbound binding re-check, context mismatch and invalid bot token.

CI evidence is pending for the exact branch SHA.

## Exact remaining blocker for full F2 acceptance

`O-05`: provision a sanctioned Telegram bot token and webhook secret in deployment secret storage, choose the public HTTPS webhook/polling deployment mode, define message data classification, and run a real private-chat pairing/send/revoke/replay sandbox E2E. No credential is requested or committed in this repository.
