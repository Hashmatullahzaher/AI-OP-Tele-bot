"""Read-only, typed REST API connector for OS AI Core F4.

The caller/model can select only pre-registered operation aliases and provide
values for pre-declared parameters. It can never supply a URL, hostname, path,
HTTP method, header name, SQL text, or redirect target.

A source-auth provider is injected by deployment code. It should derive the
source-system identity/tenant authorization from the trusted ExecutionContext;
this connector never persists credentials.
"""

from __future__ import annotations

import ipaddress
import json
import posixpath
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn, Protocol, cast

from ..contracts import CapabilityManifest, ExecutionContext, JSONValue, SourceProvenance, ToolResult


class RestConnectorError(RuntimeError):
    """Base class for sanitized REST connector failures."""


class RestAccessDenied(RestConnectorError):
    """The source denied access or the operation is outside policy."""


class RestOperationNotAllowed(RestConnectorError):
    """The requested operation/parameter is not registered."""


class RestUnavailable(RestConnectorError):
    """The source is unavailable or returned a transient server failure."""


class RestSchemaInvalid(RestConnectorError):
    """The source response does not match the configured mapping."""


class RestLimitExceeded(RestConnectorError):
    """A source response exceeded a configured bound."""


class RestHTTPError(RuntimeError):
    def __init__(self, status: int, message: str = "source request failed") -> None:
        super().__init__(message)
        self.status = status


class SourceAuthProvider(Protocol):
    def headers(self, *, context: ExecutionContext, connector_id: str) -> Mapping[str, str]:
        """Return source authorization headers for this trusted actor/tenant."""


class RestTransport(Protocol):
    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> tuple[Mapping[str, Any] | list[Any], Mapping[str, str]]:
        """GET one bounded JSON response and selected response metadata."""


@dataclass(frozen=True)
class APIParameter:
    name: str
    location: str
    required: bool = False
    max_length: int = 256
    enum: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name):
            raise ValueError("invalid parameter name")
        if self.location not in {"path", "query"}:
            raise ValueError("parameter location must be path or query")
        if self.max_length < 1 or self.max_length > 2048:
            raise ValueError("parameter max_length out of range")
        if self.enum and any(not value or len(value) > self.max_length for value in self.enum):
            raise ValueError("invalid parameter enum")


@dataclass(frozen=True)
class ResponseField:
    output_name: str
    source_name: str
    required: bool = True

    def __post_init__(self) -> None:
        for value in (self.output_name, self.source_name):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", value):
                raise ValueError("invalid response field name")


@dataclass(frozen=True)
class APIOperation:
    alias: str
    api_version: str
    path_template: str
    parameters: tuple[APIParameter, ...]
    result_path: tuple[str, ...]
    fields: tuple[ResponseField, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", self.alias):
            raise ValueError("invalid operation alias")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", self.api_version):
            raise ValueError("invalid api_version")
        if not self.path_template.startswith("/") or "?" in self.path_template or "#" in self.path_template:
            raise ValueError("path_template must be an absolute path without query/fragment")
        if "//" in self.path_template or ".." in self.path_template.split("/"):
            raise ValueError("path_template contains unsafe path segments")
        parameter_names = [item.name for item in self.parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("duplicate parameter definitions")
        placeholders = set(re.findall(r"{([a-z][a-z0-9_]*)}", self.path_template))
        declared_path = {item.name for item in self.parameters if item.location == "path"}
        if placeholders != declared_path:
            raise ValueError("path placeholders must exactly match path parameters")
        if not self.fields:
            raise ValueError("at least one response field is required")
        outputs = [field.output_name.casefold() for field in self.fields]
        if len(outputs) != len(set(outputs)):
            raise ValueError("duplicate output fields")
        if any(not part or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", part) for part in self.result_path):
            raise ValueError("invalid result_path")

    def parameter(self, name: str) -> APIParameter:
        for item in self.parameters:
            if item.name == name:
                return item
        raise RestOperationNotAllowed("parameter is not registered")


@dataclass(frozen=True)
class RESTSource:
    alias: str
    base_url: str
    allowed_hostname: str
    operations: tuple[APIOperation, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", self.alias):
            raise ValueError("invalid source alias")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("base_url must be HTTPS without userinfo")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url cannot contain query or fragment")
        host = parsed.hostname.casefold().rstrip(".")
        allowed = self.allowed_hostname.casefold().rstrip(".")
        if host != allowed:
            raise ValueError("base_url hostname must equal allowed_hostname")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
            raise ValueError("localhost is not allowed")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("literal IP addresses are not allowed")
        aliases = [op.alias for op in self.operations]
        if not aliases or len(aliases) != len(set(aliases)):
            raise ValueError("operation aliases must be non-empty and unique")

    def operation(self, alias: str) -> APIOperation:
        for operation in self.operations:
            if operation.alias == alias:
                return operation
        raise RestOperationNotAllowed("operation is not registered")


@dataclass(frozen=True)
class StaticHeaderAuthProvider:
    """Test-only auth provider. Never use a committed real credential."""

    values: Mapping[str, str]

    def headers(self, *, context: ExecutionContext, connector_id: str) -> Mapping[str, str]:
        return dict(self.values)


class UrllibRestTransport:
    """HTTPS GET transport that rejects redirects and unapproved hosts."""

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise RestHTTPError(code, "redirect refused")

    def __init__(self, *, allowed_hostname: str) -> None:
        host = allowed_hostname.casefold().rstrip(".")
        if not host:
            raise ValueError("allowed_hostname is required")
        self.allowed_hostname = host
        self._opener = urllib.request.build_opener(self._NoRedirect())

    def _request(self, url: str, headers: Mapping[str, str]) -> urllib.request.Request:
        parsed = urllib.parse.urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.hostname.casefold().rstrip(".") != self.allowed_hostname
            or parsed.username
            or parsed.password
        ):
            raise RestOperationNotAllowed("request host is not allowlisted")
        clean_headers = {"Accept": "application/json", "User-Agent": "os-ai-core/0.1"}
        for name, value in headers.items():
            if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", name):
                raise RestAccessDenied("invalid source authorization header")
            if "\r" in value or "\n" in value:
                raise RestAccessDenied("invalid source authorization header")
            clean_headers[name] = value
        return urllib.request.Request(url, headers=clean_headers, method="GET")

    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> tuple[Mapping[str, Any] | list[Any], Mapping[str, str]]:
        request = self._request(url, headers)
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                content_type = response.headers.get("Content-Type", "")
                if "application/json" not in content_type.casefold():
                    raise RestSchemaInvalid("source did not return application/json")
                payload = response.read(max_bytes + 1)
                meta = {
                    "etag": response.headers.get("ETag", ""),
                    "last-modified": response.headers.get("Last-Modified", ""),
                }
        except urllib.error.HTTPError as exc:
            raise RestHTTPError(exc.code) from exc
        except urllib.error.URLError as exc:
            raise RestUnavailable("source API unavailable") from exc
        if len(payload) > max_bytes:
            raise RestLimitExceeded("source response exceeded configured byte limit")
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RestSchemaInvalid("source returned invalid JSON") from exc
        if not isinstance(decoded, (Mapping, list)):
            raise RestSchemaInvalid("source JSON must be an object or array")
        return decoded, meta


class ClientRESTConnector:
    connector_type = "client_rest"

    def __init__(
        self,
        *,
        connector_id: str,
        source: RESTSource,
        auth_provider: SourceAuthProvider,
        transport: RestTransport | None = None,
        timeout_seconds: int = 20,
        max_response_bytes: int = 2_000_000,
        max_rows: int = 5_000,
    ) -> None:
        if not connector_id:
            raise ValueError("connector_id is required")
        if timeout_seconds < 1 or timeout_seconds > 60:
            raise ValueError("timeout_seconds out of range")
        if max_response_bytes < 1 or max_response_bytes > 10_000_000:
            raise ValueError("max_response_bytes out of range")
        if max_rows < 1 or max_rows > 20_000:
            raise ValueError("max_rows out of range")
        self.connector_id = connector_id
        self.source = source
        self.auth_provider = auth_provider
        self.transport = transport or UrllibRestTransport(allowed_hostname=source.allowed_hostname)
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.max_rows = max_rows

    def invoke(
        self,
        *,
        capability: str,
        arguments: Mapping[str, JSONValue],
        context: ExecutionContext,
    ) -> ToolResult:
        if capability != "client_api.table.read":
            raise RestOperationNotAllowed("REST capability is not supported")
        source_alias = str(arguments.get("source_alias", ""))
        operation_alias = str(arguments.get("operation_alias", ""))
        if source_alias != self.source.alias:
            raise RestOperationNotAllowed("source is not registered")
        self._require_context_scope(context)
        operation = self.source.operation(operation_alias)
        raw_parameters = arguments.get("parameters", [])
        if not isinstance(raw_parameters, list):
            raise RestOperationNotAllowed("parameters must be a list")
        parameters = self._validate_parameters(operation, raw_parameters)
        url = self._build_url(operation, parameters)
        headers = self.auth_provider.headers(context=context, connector_id=self.connector_id)
        self._validate_auth_headers(headers)
        try:
            payload, response_meta = self.transport.get_json(
                url,
                headers=headers,
                timeout_seconds=self.timeout_seconds,
                max_bytes=self.max_response_bytes,
            )
        except RestHTTPError as exc:
            self._raise_http_error(exc)
        records = self._extract_records(payload, operation)
        columns = [field.output_name for field in operation.fields]
        rows = self._map_rows(records, operation)
        revision = response_meta.get("etag") or response_meta.get("last-modified") or operation.api_version
        data: dict[str, JSONValue] = {
            "source_alias": self.source.alias,
            "operation_alias": operation.alias,
            "api_version": operation.api_version,
            "columns": cast(JSONValue, columns),
            "rows": cast(JSONValue, rows),
            "row_count": len(rows),
            "revision": str(revision),
            "source_kind": "rest_api",
        }
        provenance = SourceProvenance.observed(
            source_id=self.source.alias,
            source_type="rest_api",
            revision=str(revision),
            locator={
                "operation_alias": operation.alias,
                "api_version": operation.api_version,
            },
        )
        return ToolResult(data=data, provenance=(provenance,))

    def _require_context_scope(self, context: ExecutionContext) -> None:
        required = f"client_api:{self.source.alias}"
        scopes = set(context.resource_scope)
        if required not in scopes and "*" not in scopes:
            raise RestAccessDenied("source resource unavailable")

    def _validate_parameters(
        self,
        operation: APIOperation,
        raw_parameters: list[JSONValue],
    ) -> dict[str, str]:
        values: dict[str, str] = {}
        for raw in raw_parameters:
            if not isinstance(raw, Mapping):
                raise RestOperationNotAllowed("parameter entry must be an object")
            if set(raw) != {"name", "value"}:
                raise RestOperationNotAllowed("parameter entry has unknown fields")
            name = raw.get("name")
            value = raw.get("value")
            if not isinstance(name, str) or not isinstance(value, str):
                raise RestOperationNotAllowed("parameter name/value must be strings")
            if name in values:
                raise RestOperationNotAllowed("duplicate parameter")
            spec = operation.parameter(name)
            if len(value) > spec.max_length:
                raise RestOperationNotAllowed("parameter value exceeds limit")
            if spec.enum and value not in spec.enum:
                raise RestOperationNotAllowed("parameter value is not allowed")
            values[name] = value
        for spec in operation.parameters:
            if spec.required and spec.name not in values:
                raise RestOperationNotAllowed("required parameter is missing")
        return values

    def _build_url(self, operation: APIOperation, parameters: Mapping[str, str]) -> str:
        path = operation.path_template
        for spec in operation.parameters:
            if spec.location == "path":
                raw = parameters.get(spec.name)
                if raw is None:
                    continue
                path = path.replace("{" + spec.name + "}", urllib.parse.quote(raw, safe=""))
        if "{" in path or "}" in path:
            raise RestOperationNotAllowed("route is missing a required path parameter")
        normalized = posixpath.normpath(path)
        if not normalized.startswith("/") or normalized != path:
            raise RestOperationNotAllowed("route normalization changed configured path")
        query = []
        for spec in operation.parameters:
            if spec.location == "query" and spec.name in parameters:
                query.append((spec.name, parameters[spec.name]))
        base = self.source.base_url.rstrip("/")
        url = base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname is None or parsed.hostname.casefold().rstrip(".") != self.source.allowed_hostname.casefold().rstrip("."):
            raise RestOperationNotAllowed("constructed URL escaped source allowlist")
        return url

    @staticmethod
    def _validate_auth_headers(headers: Mapping[str, str]) -> None:
        if not headers:
            raise RestAccessDenied("source authorization unavailable")
        for name, value in headers.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise RestAccessDenied("invalid source authorization")
            if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", name) or "\r" in value or "\n" in value:
                raise RestAccessDenied("invalid source authorization")

    def _extract_records(
        self,
        payload: Mapping[str, Any] | list[Any],
        operation: APIOperation,
    ) -> list[Mapping[str, Any]]:
        current: Any = payload
        for part in operation.result_path:
            if not isinstance(current, Mapping) or part not in current:
                raise RestSchemaInvalid("configured result path is absent")
            current = current[part]
        if not isinstance(current, list):
            raise RestSchemaInvalid("configured result path must resolve to an array")
        if len(current) > self.max_rows:
            raise RestLimitExceeded("source row count exceeded configured limit")
        if not all(isinstance(item, Mapping) for item in current):
            raise RestSchemaInvalid("source result rows must be objects")
        return [cast(Mapping[str, Any], item) for item in current]

    @staticmethod
    def _map_rows(records: list[Mapping[str, Any]], operation: APIOperation) -> list[list[str]]:
        output: list[list[str]] = []
        for record in records:
            row: list[str] = []
            for field in operation.fields:
                value: Any = record
                for part in field.source_name.split("."):
                    if not isinstance(value, Mapping) or part not in value:
                        if field.required:
                            raise RestSchemaInvalid("required response field is absent")
                        value = ""
                        break
                    value = value[part]
                if isinstance(value, (Mapping, list)):
                    raise RestSchemaInvalid("mapped response fields must be scalar")
                row.append(_normalize_scalar(value))
            output.append(row)
        return output

    @staticmethod
    def _raise_http_error(exc: RestHTTPError) -> NoReturn:
        if exc.status in {401, 403, 404}:
            raise RestAccessDenied("source resource unavailable") from exc
        if exc.status == 429 or 500 <= exc.status <= 599:
            raise RestUnavailable("source API unavailable") from exc
        if 300 <= exc.status <= 399:
            raise RestOperationNotAllowed("source redirect refused") from exc
        raise RestUnavailable("source API request failed") from exc


def client_api_table_manifest() -> CapabilityManifest:
    return CapabilityManifest.from_dict(
        {
            "schema_version": "0.1",
            "connector_type": "client_rest",
            "capability": "client_api.table.read",
            "action": "READ",
            "input_schema": {
                "type": "object",
                "properties": {
                    "source_alias": {"type": "string", "minLength": 3, "maxLength": 64},
                    "operation_alias": {"type": "string", "minLength": 2, "maxLength": 64},
                    "parameters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "minLength": 1, "maxLength": 64},
                                "value": {"type": "string", "maxLength": 2048},
                            },
                            "required": ["name", "value"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["source_alias", "operation_alias", "parameters"],
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "source_alias": {"type": "string"},
                    "operation_alias": {"type": "string"},
                    "api_version": {"type": "string"},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                    "row_count": {"type": "integer", "minimum": 0},
                    "revision": {"type": "string"},
                    "source_kind": {"type": "string", "enum": ["rest_api"]},
                },
                "required": [
                    "source_alias",
                    "operation_alias",
                    "api_version",
                    "columns",
                    "rows",
                    "row_count",
                    "revision",
                    "source_kind",
                ],
                "additionalProperties": False,
            },
            "required_scopes": ["client_api:read"],
            "resource_allowlist_ref": "policyref:client-api-operation-aliases",
            "mutates": False,
            "timeout_seconds": 20,
        }
    )


def _normalize_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return format(value, ".15g")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    raise RestSchemaInvalid("mapped response field has unsupported type")
