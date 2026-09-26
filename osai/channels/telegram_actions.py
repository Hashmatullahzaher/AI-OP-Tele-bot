"""Telegram private-text bridge for permissioned AI write proposals.

This bridge receives an already-authenticated TelegramIngressResult. It never
trusts message text for tenant identity, source scope, approval truth or source
credentials. Ordinary text is treated as a write-planning request. Mutations
require a separate /approve command carrying a short-lived server-issued token.
"""

from __future__ import annotations

import json
import re

from ..actions import ActionError, ActionInProgress
from ..agent import ProviderUnavailable
from ..contracts import PolicyDenied
from ..write_agent import PermissionedWriteAgent, WriteAgentError, WritePlanInvalid
from .telegram import TelegramIngressResult


class TelegramActionBridgeError(RuntimeError):
    """Sanitized channel-to-write-agent bridge failure."""


class TelegramActionBridge:
    _APPROVE_RE = re.compile(r"^/approve\s+(tg:[A-Za-z0-9_-]{20,200})$")
    _CANCEL_RE = re.compile(r"^/cancel\s+(tg:[A-Za-z0-9_-]{20,200})$")

    def __init__(self, *, write_agent: PermissionedWriteAgent) -> None:
        self.write_agent = write_agent

    def handle(self, result: TelegramIngressResult) -> str:
        if result.kind == "paired":
            return "Telegram pairing successful. You can now send an authorized request."
        if result.kind != "message" or result.text is None:
            raise TelegramActionBridgeError("telegram message is unavailable")

        text = result.text.strip()
        approve = self._APPROVE_RE.fullmatch(text)
        if approve is not None:
            return self._approve(result, approve.group(1))

        cancel = self._CANCEL_RE.fullmatch(text)
        if cancel is not None:
            return self._cancel(result, cancel.group(1))

        if text.startswith("/approve") or text.startswith("/cancel"):
            return "Approval command is invalid or incomplete."

        try:
            proposal = self.write_agent.propose(
                message=text,
                context=result.context,
            )
        except ProviderUnavailable:
            return "The AI provider is temporarily unavailable."
        except WritePlanInvalid:
            return "I could not produce a safe write proposal from that request."
        except WriteAgentError:
            return "The write proposal could not be prepared safely."
        except PolicyDenied:
            return "You are not authorized for that action."
        except ActionError:
            return "The requested change does not satisfy the action contract."

        payload = json.dumps(
            dict(proposal.payload),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        message = (
            "PROPOSED CHANGE — nothing has been written yet.\n\n"
            f"Action: {proposal.action}\n"
            f"Payload:\n{payload}\n\n"
            f"Approve: /approve {proposal.approval_ref}\n"
            f"Cancel: /cancel {proposal.approval_ref}"
        )
        if len(message) > 4_096:
            raise TelegramActionBridgeError("proposal exceeds Telegram message limit")
        return message

    def _approve(self, result: TelegramIngressResult, approval_ref: str) -> str:
        try:
            receipt = self.write_agent.approve(
                approval_ref=approval_ref,
                context=result.context,
            )
        except WriteAgentError:
            return "Approval is invalid, expired, already used, or belongs to another user."
        except ActionInProgress:
            return "The source outcome requires reconciliation before this change can be retried."
        except PolicyDenied:
            return "You are not authorized for that action."
        except ActionError:
            return "The approved action could not be completed safely."

        return (
            "CHANGE COMPLETED.\n"
            f"Action: {receipt.action}\n"
            f"Record: {receipt.source_record_id}\n"
            f"State: {receipt.source_state}\n"
            f"Revision: {receipt.revision or '-'}"
        )

    def _cancel(self, result: TelegramIngressResult, approval_ref: str) -> str:
        try:
            self.write_agent.cancel(
                approval_ref=approval_ref,
                context=result.context,
            )
        except WriteAgentError:
            return "Approval is invalid, expired, already used, or belongs to another user."
        return "Proposed change cancelled. No source mutation was executed."
