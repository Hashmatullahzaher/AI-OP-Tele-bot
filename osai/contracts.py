"""Typed capability contract and execution boundary for OS AI Core.

This module intentionally contains no LLM, SQL generation, arbitrary URL fetch,
or shell execution path.  A model may only propose a registered capability name
and structured arguments; server-side code supplies identity and tenant context.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping, Protocol
import re
import uuid


JSONScalar = str | int | float | bool | None
JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


class ContractError(ValueError):
    """Base class for capability-contract failures."""


class SchemaValidationError(ContractError):
    """Raised when tool arguments/results violate the declared schema."""


class CapabilityNotAllowed(ContractError):
    """Raised when a capability is absent from the registered allowlist."""


class PolicyDenied(PermissionError):
    """Raised when server-side authorization denies an invocation."""


@dataclass(frozen=True)
class ExecutionContext:
    """Trusted server-side invocation metadata; never model-authored."""

    tenant_id: str
    actor_id: str
    correlation_id: str
    request_classification: str
    resource_scope: tuple[str, ...]
    session_assurance: str = "standard"

    @classmethod
    def issue(
        cls,
        *,
        tenant_id: str,
        actor_id: str,
        request_classification: str = "internal",
        resource_scope: tuple[str, ...] = (),
        session_assurance: str = "standard",
    ) -> "ExecutionContext":
        if not tenant_id or not actor_id:
            raise ContractError("tenant_id and actor_id are required")
        return cls(
            tenant_id=tenant_id,
            actor_id=actor_id,
            correlation_id=str(uuid.uuid4()),
            request_classification=request_classification,
            resource_scope=resource_scope,
            session_assurance=session_assurance,
        )


@dataclass(frozen=True)
class SourceProvenance:
    source_id: str
    source_type: str
    revision: str | None
    observed_at: str
    locator: Mapping[str, JSONValue]

    @classmethod
    def observed(
        cls,
        *,
        source_id: str,
        source_type: str,
        revision: str | None = None,
        locator: Mapping[str, JSONValue] | None = None,
    ) -> "SourceProvenance":
        return cls(
            source_id=source_id,
            source_type=source_type,
            revision=revision,
            observed_at=datetime.now(timezone.utc).isoformat(),
            locator=locator or {},
        )


@dataclass(frozen=True)
class ToolResult:
    data: Mapping[str, JSONValue]
    provenance: tuple[SourceProvenance, ...] = ()
    warnings: tuple[str, ...] = ()


class Connector(Protocol):
    connector_type: str

    def invoke(
        self,
        *,
        capability: str,
        arguments: Mapping[str, JSONValue],
        context: ExecutionContext,
    ) -> ToolResult:
        """Execute one already-authorized typed capability."""


@dataclass(frozen=True)
class CapabilityManifest:
    schema_version: str
    connector_type: str
    capability: str
    action: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    required_scopes: tuple[str, ...]
    resource_allowlist_ref: str
    mutates: bool
    timeout_seconds: int

    _FIELDS = frozenset(
        {
            "schema_version",
            "connector_type",
            "capability",
            "action",
            "input_schema",
            "output_schema",
            "required_scopes",
            "resource_allowlist_ref",
            "mutates",
            "timeout_seconds",
        }
    )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CapabilityManifest":
        unknown = set(raw) - cls._FIELDS
        missing = cls._FIELDS - set(raw)
        if unknown:
            raise ContractError(f"unknown manifest field(s): {sorted(unknown)}")
        if missing:
            raise ContractError(f"missing manifest field(s): {sorted(missing)}")
        action = str(raw["action"]).upper()
        mutates = bool(raw["mutates"])
        if action not in {"READ", "WRITE"}:
            raise ContractError("action must be READ or WRITE")
        if (action == "READ" and mutates) or (action == "WRITE" and not mutates):
            raise ContractError("action and mutates disagree")
        capability = str(raw["capability"])
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,127}", capability):
            raise ContractError("capability name is invalid")
        connector_type = str(raw["connector_type"])
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", connector_type):
            raise ContractError("connector_type is invalid")
        timeout = int(raw["timeout_seconds"])
        if timeout < 1 or timeout > 120:
            raise ContractError("timeout_seconds must be between 1 and 120")
        scopes = raw["required_scopes"]
        if not isinstance(scopes, list) or not scopes or not all(isinstance(v, str) and v for v in scopes):
            raise ContractError("required_scopes must be a non-empty list of non-empty strings")
        input_schema = raw["input_schema"]
        output_schema = raw["output_schema"]
        if not isinstance(input_schema, Mapping) or not isinstance(output_schema, Mapping):
            raise ContractError("input_schema/output_schema must be objects")
        # Validate schema shape using an empty object only when no required fields exist.
        _validate_schema_definition(input_schema)
        _validate_schema_definition(output_schema)
        allowlist_ref = str(raw["resource_allowlist_ref"])
        if not allowlist_ref:
            raise ContractError("resource_allowlist_ref is required")
        return cls(
            schema_version=str(raw["schema_version"]),
            connector_type=connector_type,
            capability=capability,
            action=action,
            input_schema=input_schema,
            output_schema=output_schema,
            required_scopes=tuple(scopes),
            resource_allowlist_ref=allowlist_ref,
            mutates=mutates,
            timeout_seconds=timeout,
        )


def _validate_schema_definition(schema: Mapping[str, Any], path: str = "$schema") -> None:
    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
    }
    unknown = set(schema) - allowed
    if unknown:
        raise ContractError(f"{path}: unsupported schema keyword(s): {sorted(unknown)}")
    stype = schema.get("type")
    if stype not in {"object", "array", "string", "number", "integer", "boolean", "null"}:
        raise ContractError(f"{path}: unsupported or missing schema type")
    if stype == "object":
        props = schema.get("properties", {})
        if not isinstance(props, Mapping):
            raise ContractError(f"{path}.properties must be an object")
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(v, str) for v in required):
            raise ContractError(f"{path}.required must be a string list")
        if not set(required).issubset(set(props)):
            raise ContractError(f"{path}.required refers to an unknown property")
        if schema.get("additionalProperties", False) is not False:
            raise ContractError(f"{path}: additionalProperties must be false")
        for name, child in props.items():
            if not isinstance(child, Mapping):
                raise ContractError(f"{path}.properties.{name} must be an object")
            _validate_schema_definition(child, f"{path}.properties.{name}")
    if stype == "array":
        child = schema.get("items")
        if not isinstance(child, Mapping):
            raise ContractError(f"{path}.items must be an object")
        _validate_schema_definition(child, f"{path}.items")


def validate_value(schema: Mapping[str, Any], value: Any, path: str = "$") -> None:
    """Validate the deliberately small JSON-schema subset used by tool contracts."""

    stype = schema["type"]
    if stype == "object":
        if not isinstance(value, Mapping):
            raise SchemaValidationError(f"{path}: expected object")
        props = schema.get("properties", {})
        unknown = set(value) - set(props)
        if unknown:
            raise SchemaValidationError(f"{path}: unknown field(s): {sorted(unknown)}")
        for req in schema.get("required", []):
            if req not in value:
                raise SchemaValidationError(f"{path}: missing required field {req!r}")
        for key, item in value.items():
            validate_value(props[key], item, f"{path}.{key}")
        return
    if stype == "array":
        if not isinstance(value, list):
            raise SchemaValidationError(f"{path}: expected array")
        for idx, item in enumerate(value):
            validate_value(schema["items"], item, f"{path}[{idx}]")
        return
    if stype == "string":
        if not isinstance(value, str):
            raise SchemaValidationError(f"{path}: expected string")
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            raise SchemaValidationError(f"{path}: string shorter than minLength")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise SchemaValidationError(f"{path}: string longer than maxLength")
    elif stype == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaValidationError(f"{path}: expected integer")
    elif stype == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise SchemaValidationError(f"{path}: expected number")
    elif stype == "boolean":
        if not isinstance(value, bool):
            raise SchemaValidationError(f"{path}: expected boolean")
    elif stype == "null":
        if value is not None:
            raise SchemaValidationError(f"{path}: expected null")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path}: value is not in enum")
    if stype in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaValidationError(f"{path}: value below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaValidationError(f"{path}: value above maximum")


PolicyCheck = Callable[[ExecutionContext, CapabilityManifest], None]


class CapabilityRegistry:
    """Explicit allowlist mapping capability names to typed connectors."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[CapabilityManifest, Connector]] = {}

    def register(self, manifest: CapabilityManifest, connector: Connector) -> None:
        if manifest.connector_type != connector.connector_type:
            raise ContractError("manifest connector_type does not match connector")
        if manifest.capability in self._entries:
            raise ContractError(f"capability already registered: {manifest.capability}")
        self._entries[manifest.capability] = (manifest, connector)

    def resolve(self, capability: str) -> tuple[CapabilityManifest, Connector]:
        try:
            return self._entries[capability]
        except KeyError as exc:
            raise CapabilityNotAllowed("capability is not registered") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))


class ToolExecutor:
    """Validate -> authorize -> invoke -> validate, with no dynamic escape hatch."""

    def __init__(self, registry: CapabilityRegistry, policy_check: PolicyCheck | None = None) -> None:
        self._registry = registry
        self._policy_check = policy_check

    def execute(
        self,
        *,
        capability: str,
        arguments: Mapping[str, JSONValue],
        context: ExecutionContext,
    ) -> ToolResult:
        manifest, connector = self._registry.resolve(capability)
        validate_value(manifest.input_schema, arguments)
        if self._policy_check is not None:
            self._policy_check(context, manifest)
        result = connector.invoke(capability=capability, arguments=arguments, context=context)
        validate_value(manifest.output_schema, result.data)
        return result
