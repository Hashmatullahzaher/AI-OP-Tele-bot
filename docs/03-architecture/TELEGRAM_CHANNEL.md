# Telegram private-text channel architecture

F2 treats Telegram as a **channel**, never as an identity authority and never as a source of tenant permissions.

## Trust flow

```text
Telegram HTTPS webhook
 -> runtime bot alias
 -> X-Telegram-Bot-Api-Secret-Token constant-time verification
 -> JSON/update_id validation
 -> durable replay claim
 -> private chat + text-only gate
 -> out-of-band paired chat/user -> tenant actor
 -> server-issued ExecutionContext
 -> AI/core workflow
 -> active binding re-check
 -> runtime bot token
 -> Telegram sendMessage
```

## Pairing

A trusted admin workflow creates a high-entropy pairing code for one active tenant actor and one non-secret, globally unique `bot_alias`. The database stores only SHA-256 of `bot_alias:code`, expiry, target actor and single-use state. The user sends `/pair <code>` in a private chat. An active chat cannot silently be rebound; it must be revoked first.

Pairing codes expire in 1–60 minutes; the default is 10 minutes. Actor revocation immediately makes an existing binding unusable.

## Replay and chat policy

Every authenticated Telegram `update_id` is atomically claimed in SQLite before message handling. A duplicate update fails closed. F2 accepts only the `message` object, `chat.type=private`, positive user/chat/message IDs and non-empty text up to Telegram's 4096-character limit. Groups, channels, voice, files, edited messages and callbacks are not silently accepted in this milestone.

## Secrets

Webhook secrets and bot tokens are runtime provider values. They are never stored in source code, pairing tables, audit records, or user-visible errors. The Telegram Bot API token is used only to construct the outbound HTTPS request and must not be logged.

## Logging and audit

Channel security events carry only event type, bot alias and update ID where available. Message text and pairing code are never included. Once a tenant actor is known, tenant audit records store only digests of minimal metadata and do not store the message body.

## Outbound authorization

Outbound text requires an active Telegram binding matching the server-issued tenant/actor context, chat ID and Telegram user ID. Revoked/mismatched bindings fail before a token is used.

## Current live gate

The implementation is testable without a real bot credential. O-05 remains the exact blocker for sandbox E2E: create a sanctioned bot, choose webhook HTTPS vs approved polling deployment, store bot token/webhook secret in the deployment secret manager, and confirm pairing/data-classification rules.
