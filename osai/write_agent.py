"""Provider-neutral AI planning for permissioned write proposals.

The model may propose only one registered F9 action and structured arguments. It
never receives tenant IDs, actor IDs, credentials, approval truth or filesystem
paths. No mutation occurs during proposal generation. A separate short-lived
approval must be presented before ActionCoordinator.execute() is called.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from .action_approval import PendingApprovalStore, PendingApprovalUnavailable
from .actions import ActionCoordinator, ActionReceipt
from .agent import AuditSink, ModelProvider, ProviderUnavailable, ToolCatalogEntry
from .contracts import ExecutionContext, JSONValue


class WriteAgentError(RuntimeError):
    """Sanitized provider/proposal/approval failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WritePlanInvalid(WriteAgentError):
    def __init__(self, message: str = "AI write plan is invalid") -> None:
        super().__init__("WRITE_PLAN_INVALID", message)


@dataclass(frozen=True)
class WritePlan:
    capability: str
    arguments: Mapping[str, JSONValue]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> WritePlan:
        allowed = {"schema_version", "action", "capability", "arguments"}
        if set(raw) != allowed:
            raise WritePlanInvalid("write plan fields are missing or unknown")
        if raw.get("schema_version") != "0.1" or raw.get("action") != "write":
            raise WritePlanInvalid("only schema 0.1 write plans are supported")
        capability = raw.get("capability")
        arguments = raw.get("arguments")
        if not isinstance(capability, str) or not re.fullmatch(
            r"[a-z][a-z0-9_.-]{2,127}", capability
        ):
            raise WritePlanInvalid("write capability is invalid")
        if not isinstance(arguments, Mapping) or not all(
            isinstance(key, str) for key in arguments
        ):
            raise WritePlanInvalid("write arguments must be an object")
        try:
            serialized = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise WritePlanInvalid("write arguments are not valid JSON values") from exc
        if len(serialized) > 8_000:
            raise WritePlanInvalid("write proposal exceeds safe Telegram review size")
        return cls(
            capability=capability,
            arguments=cast(Mapping[str, JSONValue], dict(arguments)),
        )


@dataclass(frozen=True)
class WriteProposal:
    action: str
    payload: Mapping[str, JSONValue]
    approval_ref: str
    expires_at: int
    provider_name: str
    correlation_id: str


class PermissionedWriteAgent:
    """AI proposes; server validates; human approves; F9 executes."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        coordinator: ActionCoordinator,
        approval_store: PendingApprovalStore,
        audit_sink: AuditSink,
        source_scope: str,
        max_message_chars: int = 4_000,
    ) -> None:
        if not source_scope.startswith("client_api:"):
            raise ValueError("source_scope must be a client_api scope")
        if max_message_chars < 1 or max_message_chars > 4_096:
            raise ValueError("max_message_chars out of range")
        self.provider = provider
        self.coordinator = coordinator
        self.approval_store = approval_store
        self.audit_sink = audit_sink
        self.source_scope = source_scope
        self.max_message_chars = max_message_chars

    def _context(self, incoming: ExecutionContext, *, approved: bool = False) -> ExecutionContext:
        return ExecutionContext(
            tenant_id=incoming.tenant_id,
            actor_id=incoming.actor_id,
            correlation_id=incoming.correlation_id,
            request_classification=incoming.request_classification,
            resource_scope=(self.source_scope,),
            session_assurance="telegram-step-up" if approved else incoming.session_assurance,
        )

    def _catalog(self) -> tuple[ToolCatalogEntry, ...]:
        entries: list[ToolCatalogEntry] = []
        for action in sorted(self.coordinator.definitions):
            definition = self.coordinator.definitions[action]
            entries.append(
                ToolCatalogEntry(
                    capability=definition.capability,
                    connector_type="action",
                    action="WRITE",
                    input_schema=definition.input_schema,
                )
            )
        return tuple(entries)

    def propose(self, *, message: str, context: ExecutionContext) -> WriteProposal:
        clean = message.strip()
        if not clean or len(clean) > self.max_message_chars:
            raise WriteAgentError("REQUEST_INVALID", "message is empty or too long")
        catalog = self._catalog()
        try:
            raw = self.provider.plan(
                user_message=clean,
                tool_catalog=catalog,
                correlation_id=context.correlation_id,
            )
        except ProviderUnavailable:
            raise
        except Exception as exc:
            raise ProviderUnavailable() from exc
        if not isinstance(raw, Mapping):
            raise WritePlanInvalid()
        plan = WritePlan.from_mapping(raw)
        allowed = {entry.capability for entry in catalog}
        if plan.capability not in allowed:
            raise WritePlanInvalid("provider selected a capability outside the write catalog")

        scoped = self._context(context)
        prepared = self.coordinator.prepare(
            context=scoped,
            action=plan.capability,
            payload=plan.arguments,
        )
        review_json = json.dumps(
            dict(prepared.normalized_payload),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        if len(review_json) > 2_600:
            raise WritePlanInvalid(
                "write proposal is too large for complete Telegram review"
            )
        pending = self.approval_store.issue(
            context=scoped,
            prepared=prepared,
            request_payload=plan.arguments,
        )
        self.audit_sink.append_audit(
            context=scoped,
            event="agent.write_proposed",
            capability=prepared.definition.capability,
            resource_scope=self.source_scope,
            result="PENDING_APPROVAL",
            sensitive_payload={"payload_digest": prepared.payload_digest},
        )
        return WriteProposal(
            action=prepared.action,
            payload=dict(prepared.normalized_payload),
            approval_ref=pending.approval_ref,
            expires_at=pending.expires_at,
            provider_name=self.provider.provider_name,
            correlation_id=context.correlation_id,
        )

    def approve(
        self,
        *,
        approval_ref: str,
        context: ExecutionContext,
    ) -> ActionReceipt:
        scoped = self._context(context, approved=True)
        try:
            pending = self.approval_store.resolve(
                context=scoped,
                approval_ref=approval_ref,
            )
        except PendingApprovalUnavailable as exc:
            raise WriteAgentError("APPROVAL_UNAVAILABLE", "approval is invalid or expired") from exc
        return self.coordinator.execute(
            context=scoped,
            action=pending.action,
            payload=pending.payload,
            approval_ref=approval_ref,
            idempotency_key=pending.idempotency_key,
        )

    def cancel(
        self,
        *,
        approval_ref: str,
        context: ExecutionContext,
    ) -> None:
        scoped = self._context(context)
        try:
            pending = self.approval_store.resolve(
                context=scoped,
                approval_ref=approval_ref,
            )
            self.approval_store.cancel(
                context=scoped,
                approval_ref=approval_ref,
            )
        except PendingApprovalUnavailable as exc:
            raise WriteAgentError("APPROVAL_UNAVAILABLE", "approval is invalid or expired") from exc
        self.audit_sink.append_audit(
            context=scoped,
            event="agent.write_cancelled",
            capability=pending.action,
            resource_scope=self.source_scope,
            result="CANCELLED",
            sensitive_payload={"payload_digest": pending.payload_digest},
        )
