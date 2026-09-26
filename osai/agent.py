"""Provider-neutral, source-grounded AI orchestration for OS AI Core F5.

The model is a planner, not an authority. It may select exactly one registered
typed capability and a deterministic post-processing operation. The server
supplies tenant/actor context, executes the tool through the existing policy
boundary, performs financial arithmetic with Decimal, and renders the
authoritative answer itself.

Source/tool output is never fed back to the model in this foundation, so prompt
text embedded in documents/API fields cannot recursively register or invoke a
new tool. A future narrative renderer may be added only behind the same facts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, cast

from .connectors.google_drive import DriveAccessDenied, DriveSchemaAmbiguous, DriveUnavailable
from .connectors.rest_api import RestAccessDenied, RestSchemaInvalid, RestUnavailable
from .contracts import (
    CapabilityNotAllowed,
    CapabilityRegistry,
    ExecutionContext,
    JSONValue,
    PolicyCheck,
    PolicyDenied,
    SchemaValidationError,
    ToolExecutor,
    validate_value,
    ToolResult,
)


class AgentError(RuntimeError):
    """Sanitized user-facing agent failure with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProviderUnavailable(AgentError):
    def __init__(self, message: str = "AI provider unavailable") -> None:
        super().__init__("PROVIDER_UNAVAILABLE", message)


class AgentPlanInvalid(AgentError):
    def __init__(self, message: str = "AI plan is invalid") -> None:
        super().__init__("PLAN_INVALID", message)


class AuditSink(Protocol):
    def append_audit(
        self,
        *,
        context: ExecutionContext,
        event: str,
        result: str,
        capability: str | None = None,
        resource_scope: str | None = None,
        sensitive_payload: Mapping[str, Any] | None = None,
    ) -> int:
        """Persist a redacted/digested audit event before data is disclosed."""


class ModelProvider(Protocol):
    provider_name: str

    def plan(
        self,
        *,
        user_message: str,
        tool_catalog: Sequence["ToolCatalogEntry"],
        correlation_id: str,
    ) -> Mapping[str, Any]:
        """Return one strict plan. It never receives trusted execution context."""


@dataclass(frozen=True)
class ToolCatalogEntry:
    capability: str
    connector_type: str
    action: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class FilterSpec:
    column: str
    equals: str


@dataclass(frozen=True)
class AnalysisSpec:
    kind: str
    column: str | None = None
    currency_column: str | None = None
    filters: tuple[FilterSpec, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "AnalysisSpec":
        if raw is None:
            return cls(kind="table")
        allowed = {"kind", "column", "currency_column", "filters"}
        if set(raw) - allowed:
            raise AgentPlanInvalid("analysis contains unknown fields")
        kind = raw.get("kind")
        if kind not in {"table", "count", "sum"}:
            raise AgentPlanInvalid("analysis kind is unsupported")
        column_raw = raw.get("column")
        currency_raw = raw.get("currency_column")
        column = None if column_raw in {None, ""} else str(column_raw)
        currency_column = None if currency_raw in {None, ""} else str(currency_raw)
        if kind == "sum" and not column:
            raise AgentPlanInvalid("sum analysis requires a column")
        if kind != "sum" and column is not None:
            raise AgentPlanInvalid("column is only valid for sum analysis")
        if kind != "sum" and currency_column is not None:
            raise AgentPlanInvalid("currency_column is only valid for sum analysis")
        filters_raw = raw.get("filters", [])
        if not isinstance(filters_raw, list) or len(filters_raw) > 16:
            raise AgentPlanInvalid("analysis filters must be a bounded list")
        filters: list[FilterSpec] = []
        for item in filters_raw:
            if not isinstance(item, Mapping) or set(item) != {"column", "equals"}:
                raise AgentPlanInvalid("analysis filter shape is invalid")
            filter_column = item.get("column")
            equals = item.get("equals")
            if not isinstance(filter_column, str) or not isinstance(equals, str):
                raise AgentPlanInvalid("analysis filter values must be strings")
            if not filter_column or len(filter_column) > 128 or len(equals) > 512:
                raise AgentPlanInvalid("analysis filter exceeds limits")
            filters.append(FilterSpec(column=filter_column, equals=equals))
        return cls(
            kind=cast(str, kind),
            column=column,
            currency_column=currency_column,
            filters=tuple(filters),
        )


@dataclass(frozen=True)
class AgentPlan:
    capability: str
    arguments: Mapping[str, JSONValue]
    analysis: AnalysisSpec

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AgentPlan":
        allowed = {"schema_version", "action", "capability", "arguments", "analysis"}
        if set(raw) != allowed:
            raise AgentPlanInvalid("plan fields are missing or unknown")
        if raw.get("schema_version") != "0.1" or raw.get("action") != "tool":
            raise AgentPlanInvalid("only schema 0.1 single-tool plans are supported")
        capability = raw.get("capability")
        arguments = raw.get("arguments")
        analysis = raw.get("analysis")
        if not isinstance(capability, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{2,127}", capability):
            raise AgentPlanInvalid("plan capability is invalid")
        if not isinstance(arguments, Mapping):
            raise AgentPlanInvalid("plan arguments must be an object")
        if not all(isinstance(key, str) for key in arguments):
            raise AgentPlanInvalid("plan argument keys must be strings")
        if not isinstance(analysis, Mapping):
            raise AgentPlanInvalid("plan analysis must be an object")
        return cls(
            capability=capability,
            arguments=cast(Mapping[str, JSONValue], dict(arguments)),
            analysis=AnalysisSpec.from_mapping(analysis),
        )


@dataclass(frozen=True)
class GroundedSource:
    source_id: str
    source_type: str
    revision: str | None
    locator: Mapping[str, JSONValue]


@dataclass(frozen=True)
class GroundedAnswer:
    text: str
    authoritative_data: Mapping[str, JSONValue]
    sources: tuple[GroundedSource, ...]
    warnings: tuple[str, ...]
    correlation_id: str
    provider_name: str
    capability: str


class SourceGroundedAgent:
    """One-request/one-tool source-grounded orchestrator."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        registry: CapabilityRegistry,
        executor: ToolExecutor,
        policy_check: PolicyCheck,
        audit_sink: AuditSink,
        max_message_chars: int = 4_000,
    ) -> None:
        if max_message_chars < 1 or max_message_chars > 32_000:
            raise ValueError("max_message_chars out of range")
        self.provider = provider
        self.registry = registry
        self.executor = executor
        self.policy_check = policy_check
        self.audit_sink = audit_sink
        self.max_message_chars = max_message_chars

    def ask(self, *, message: str, context: ExecutionContext) -> GroundedAnswer:
        clean = message.strip()
        if not clean or len(clean) > self.max_message_chars:
            raise AgentError("REQUEST_INVALID", "message is empty or too long")
        catalog = self._catalog()
        try:
            raw_plan = self.provider.plan(
                user_message=clean,
                tool_catalog=catalog,
                correlation_id=context.correlation_id,
            )
        except ProviderUnavailable:
            raise
        except Exception as exc:
            raise ProviderUnavailable() from exc
        if not isinstance(raw_plan, Mapping):
            raise AgentPlanInvalid()
        plan = AgentPlan.from_mapping(raw_plan)
        if plan.capability not in {entry.capability for entry in catalog}:
            raise AgentPlanInvalid("provider selected a capability outside the catalog")
        manifest, _ = self.registry.resolve(plan.capability)
        try:
            validate_value(manifest.input_schema, plan.arguments)
        except SchemaValidationError as exc:
            raise AgentPlanInvalid("provider arguments violate the tool contract") from exc
        try:
            self.policy_check(context, manifest)
        except PolicyDenied as exc:
            raise AgentError("SOURCE_ACCESS_DENIED", "source access denied") from exc
        try:
            result = self.executor.execute(
                capability=plan.capability,
                arguments=plan.arguments,
                context=context,
            )
        except CapabilityNotAllowed as exc:
            raise AgentError("CAPABILITY_NOT_ALLOWED", "requested capability is unavailable") from exc
        except PolicyDenied as exc:
            raise AgentError("SOURCE_ACCESS_DENIED", "source access denied") from exc
        except (DriveAccessDenied, RestAccessDenied) as exc:
            raise AgentError("SOURCE_ACCESS_DENIED", "source access denied") from exc
        except (DriveUnavailable, RestUnavailable) as exc:
            raise AgentError("SOURCE_UNAVAILABLE", "source unavailable") from exc
        except (DriveSchemaAmbiguous, RestSchemaInvalid, SchemaValidationError) as exc:
            raise AgentError("SOURCE_SCHEMA_AMBIGUOUS", "source schema cannot be used safely") from exc
        authoritative, text = analyze_tool_result(result, plan.analysis)
        sources = tuple(
            GroundedSource(
                source_id=item.source_id,
                source_type=item.source_type,
                revision=item.revision,
                locator=item.locator,
            )
            for item in result.provenance
        )
        if not sources:
            raise AgentError("SOURCE_UNAVAILABLE", "tool result has no source provenance")
        self.audit_sink.append_audit(
            context=context,
            event="agent.source_read",
            capability=plan.capability,
            resource_scope=",".join(sorted(context.resource_scope)),
            result="OK",
            sensitive_payload={
                "analysis_kind": plan.analysis.kind,
                "source_count": len(sources),
            },
        )
        return GroundedAnswer(
            text=text,
            authoritative_data=authoritative,
            sources=sources,
            warnings=result.warnings,
            correlation_id=context.correlation_id,
            provider_name=self.provider.provider_name,
            capability=plan.capability,
        )

    def _catalog(self) -> tuple[ToolCatalogEntry, ...]:
        entries = []
        for name in self.registry.names():
            manifest, _ = self.registry.resolve(name)
            if manifest.action != "READ" or manifest.mutates:
                continue
            entries.append(
                ToolCatalogEntry(
                    capability=manifest.capability,
                    connector_type=manifest.connector_type,
                    action=manifest.action,
                    input_schema=manifest.input_schema,
                )
            )
        return tuple(entries)


def analyze_tool_result(
    result: ToolResult,
    analysis: AnalysisSpec,
) -> tuple[dict[str, JSONValue], str]:
    """Perform deterministic table filtering/counting/summing."""

    columns_raw = result.data.get("columns")
    rows_raw = result.data.get("rows")
    if not isinstance(columns_raw, list) or not all(isinstance(value, str) for value in columns_raw):
        raise AgentError("SOURCE_SCHEMA_AMBIGUOUS", "tool result does not expose table columns")
    if not isinstance(rows_raw, list) or not all(isinstance(row, list) for row in rows_raw):
        raise AgentError("SOURCE_SCHEMA_AMBIGUOUS", "tool result does not expose table rows")
    columns = cast(list[str], columns_raw)
    if not columns or len(columns) != len(set(columns)):
        raise AgentError("SOURCE_SCHEMA_AMBIGUOUS", "tool result columns are ambiguous")
    rows: list[list[str]] = []
    for row in rows_raw:
        if len(row) != len(columns) or not all(isinstance(value, str) for value in row):
            raise AgentError("SOURCE_SCHEMA_AMBIGUOUS", "tool result row shape is invalid")
        rows.append(cast(list[str], row))
    index = {name: offset for offset, name in enumerate(columns)}
    filtered = rows
    for filter_spec in analysis.filters:
        if filter_spec.column not in index:
            raise AgentError("SOURCE_SCHEMA_AMBIGUOUS", "analysis filter column is absent")
        offset = index[filter_spec.column]
        filtered = [row for row in filtered if row[offset] == filter_spec.equals]

    if analysis.kind == "table":
        data: dict[str, JSONValue] = {
            "kind": "table",
            "columns": cast(JSONValue, columns),
            "rows": cast(JSONValue, filtered),
            "row_count": len(filtered),
        }
        return data, f"Retrieved {len(filtered)} source row(s)."

    if analysis.kind == "count":
        data = {"kind": "count", "count": len(filtered)}
        return data, f"Count = {len(filtered)}."

    if analysis.column is None or analysis.column not in index:
        raise AgentError("SOURCE_SCHEMA_AMBIGUOUS", "sum column is absent")
    amount_offset = index[analysis.column]
    total = Decimal("0")
    for row in filtered:
        raw = row[amount_offset].strip()
        if not raw:
            continue
        try:
            total += Decimal(raw)
        except InvalidOperation as exc:
            raise AgentError("SOURCE_SCHEMA_AMBIGUOUS", "sum column contains a non-numeric value") from exc

    currency: str | None = None
    if analysis.currency_column is not None:
        if analysis.currency_column not in index:
            raise AgentError("SOURCE_SCHEMA_AMBIGUOUS", "currency column is absent")
        currency_offset = index[analysis.currency_column]
        currencies = {row[currency_offset].strip() for row in filtered if row[currency_offset].strip()}
        if len(currencies) > 1:
            raise AgentError("SOURCE_SCHEMA_AMBIGUOUS", "financial sum spans multiple currencies")
        if currencies:
            currency = next(iter(currencies))

    amount = _decimal_text(total)
    data = {
        "kind": "sum",
        "column": analysis.column,
        "amount": amount,
        "currency": currency,
        "matched_rows": len(filtered),
    }
    suffix = f" {currency}" if currency else ""
    return data, f"Sum of {analysis.column} = {amount}{suffix} across {len(filtered)} matching row(s)."


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return format(normalized, "f")
    return format(normalized, "f")
