# Telegram to AI planning to approval to Excel UAT

Status: FULL ORCHESTRATION HARNESS — SYNTHETIC PLANNER, NOT LIVE TELEGRAM/LLM

This UAT extends the Excel sandbox so the write path starts from the existing Telegram ingress boundary instead of calling F9 directly.

Tested path:

Telegram webhook update -> webhook-secret validation -> replay protection -> paired Telegram identity -> provider-neutral write planner -> server-side F9 action preparation -> short-lived payload-bound approval token -> separate /approve message from the same paired actor -> F9 capability/financial/idempotency controls -> Excel sandbox source adapter -> XLSX mutation + source-side SystemJournal -> durable OS AI Core audit.

The first message never mutates the workbook. It returns the exact normalized payload and an approval command. Only a later /approve <token> message from the same paired tenant actor may execute the change.

## Current planner

The executable UAT uses DeterministicUATPlanningProvider. It implements the same ModelProvider protocol as the AI layer but is intentionally scripted. This proves the orchestration/security plumbing without putting a real model API key in CI.

Therefore this milestone does not claim that a live LLM understood free-form Dari/English requests. That remains behind the existing model-provider gate.

## Approval properties

- token plaintext is returned once through the channel;
- only a SHA-256 token hash is stored;
- token is bound to tenant, actor, action and normalized payload digest;
- token expires after a short TTL;
- token is single-use;
- cancel consumes the token without mutation;
- a token issued to one paired Telegram actor cannot be used by another actor;
- F9 source/core idempotency remains independent of the approval token.

## Windows CI evidence

The Windows runner executes osai.telegram_excel_uat and stores both the resulting XLSX workbook and JSON transcript as short-lived CI artifacts.

The UAT pairs a synthetic Telegram identity, then proposes and separately approves:

1. customer create;
2. customer update;
3. procurement request create;
4. balanced draft voucher create.

## Remaining live gates

To turn this exact path into a real interactive Telegram test we still need:
- a dedicated test Telegram bot token and webhook secret;
- a reachable HTTPS webhook or an explicitly designed polling runner;
- an approved live model/provider credential and model policy.

Those secrets must never be committed to Git.