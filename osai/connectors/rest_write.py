"""Typed REST mutation adapter for OS AI Core F9.

Only server-configured action aliases and fields are allowed. Callers cannot
supply arbitrary URLs, methods, headers, SQL, or redirect targets.
"""

from __future__ import annotations

import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..actions import ActionReceipt
from ..contracts import ExecutionContext, JSONValue


class WriteConnectorError(RuntimeError):
    """Base class for sanitized write connector failures."""


class WriteAccessDenied(WriteConnectorError):
    """Source authorization or configured write policy denied the action."""


class WriteOperationNotAllowed(WriteConnectorError):
    """Action/field is not part of the registered mutation contract."""


class WriteUnavailable(WriteConnectorError):
    """Source outcome may be unknown after dispatch; reconciliation is required."""

    outcome_uncertain = True


class WriteSchemaInvalid(WriteConnectorError):
    """Source may have committed but returned an unusable receipt."""

    outcome_uncertain = True


class WriteHTTPError(RuntimeError):
    def __init__(self, status: int, message: str = "source mutation failed") -> None:
        super().__init__(message)
        self.status = status


class SourceWriteAuthProvider(Protocol):
    def headers(self, *, context: ExecutionContext, connector_id: str) -> Mapping[str, str]:
        """Return source-system credentials scoped to the trusted actor/tenant."""


class WriteTransport(Protocol):
    def send_json(
        self,
        url: str,
        *,
        method: str,
        headers: Mapping[str, str],
        payload: Mapping[str, JSONValue],
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> Mapping[str, Any]:
        """Send one bounded HTTPS mutation and return its JSON receipt."""


@dataclass(frozen=True)
class WriteOperation:
    action: str
    method: str
    path: str
    allowed_fields: tuple[str, ...]
    required_fields: tuple[str, ...]
    record_id_field: str
    state_field: str
    tenant_field: str
    actor_field: str
    idempotency_field: str
    revision_field: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,127}", self.action):
            raise ValueError("invalid action")
        if self.method not in {"POST", "PATCH"}:
            raise ValueError("write method must be POST or PATCH")
        if not self.path.startswith("/") or "?" in self.path or "#" in self.path:
            raise ValueError("write path must be absolute without query/fragment")
        if "//" in self.path or ".." in self.path.split("/"):
            raise ValueError("unsafe write path")
        allowed = set(self.allowed_fields)
        if not allowed or len(allowed) != len(self.allowed_fields):
            raise ValueError("allowed_fields must be non-empty and unique")
        if not set(self.required_fields).issubset(allowed):
            raise ValueError("required_fields must be part of allowed_fields")
        for field in (
            self.record_id_field,
            self.state_field,
            self.tenant_field,
            self.actor_field,
            self.idempotency_field,
        ):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", field):
                raise ValueError("invalid receipt field")
        if self.revision_field and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", self.revision_field):
            raise ValueError("invalid revision field")


@dataclass(frozen=True)
class WriteSource:
    alias: str
    base_url: str
    allowed_hostname: str
    operations: tuple[WriteOperation, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", self.alias):
            raise ValueError("invalid source alias")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("base_url must be HTTPS without userinfo")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url cannot contain query/fragment")
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
        actions = [item.action for item in self.operations]
        if not actions or len(actions) != len(set(actions)):
            raise ValueError("write actions must be non-empty and unique")

    def operation(self, action: str) -> WriteOperation:
        for item in self.operations:
            if item.action == action:
                return item
        raise WriteOperationNotAllowed("write action is not configured")


@dataclass(frozen=True)
class StaticWriteAuthProvider:
    """Test-only auth provider. Never commit a real source credential."""

    values: Mapping[str, str]

    def headers(self, *, context: ExecutionContext, connector_id: str) -> Mapping[str, str]:
        return dict(self.values)


class UrllibWriteTransport:
    """HTTPS-only mutation transport with redirects disabled."""

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise WriteHTTPError(code, "redirect refused")

    def __init__(self, *, allowed_hostname: str) -> None:
        self.allowed_hostname = allowed_hostname.casefold().rstrip(".")
        self._opener = urllib.request.build_opener(self._NoRedirect())

    def send_json(
        self,
        url: str,
        *,
        method: str,
        headers: Mapping[str, str],
        payload: Mapping[str, JSONValue],
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> Mapping[str, Any]:
        parsed = urllib.parse.urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.hostname.casefold().rstrip(".") != self.allowed_hostname
            or parsed.username
            or parsed.password
        ):
            raise WriteOperationNotAllowed("write host is not allowlisted")
        clean_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "os-ai-core/0.1",
        }
        for name, value in headers.items():
            if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", name):
                raise WriteAccessDenied("invalid source authorization header")
            if "\r" in value or "\n" in value:
                raise WriteAccessDenied("invalid source authorization header")
            clean_headers[name] = value
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=clean_headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                content_type = response.headers.get("Content-Type", "")
                if "application/json" not in content_type.casefold():
                    raise WriteSchemaInvalid("source did not return application/json")
                raw = response.read(max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise WriteHTTPError(exc.code) from exc
        except urllib.error.URLError as exc:
            raise WriteUnavailable("source system unavailable") from exc
        if len(raw) > max_response_bytes:
            raise WriteSchemaInvalid("source response exceeded configured limit")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WriteSchemaInvalid("source returned invalid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise WriteSchemaInvalid("source receipt must be a JSON object")
        return decoded


class ClientRESTWriteAdapter:
    """Execute only pre-registered mutation operations on one client source."""

    def __init__(
        self,
        *,
        connector_id: str,
        source: WriteSource,
        auth_provider: SourceWriteAuthProvider,
        transport: WriteTransport | None = None,
        timeout_seconds: int = 30,
        max_response_bytes: int = 512_000,
    ) -> None:
        if not connector_id:
            raise ValueError("connector_id is required")
        if timeout_seconds < 1 or timeout_seconds > 60:
            raise ValueError("timeout_seconds out of range")
        if max_response_bytes < 1 or max_response_bytes > 2_000_000:
            raise ValueError("max_response_bytes out of range")
        self.connector_id = connector_id
        self.source = source
        self.auth_provider = auth_provider
        self.transport = transport or UrllibWriteTransport(allowed_hostname=source.allowed_hostname)
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    def execute(
        self,
        *,
        action: str,
        payload: Mapping[str, JSONValue],
        context: ExecutionContext,
        idempotency_key: str,
    ) -> ActionReceipt:
        operation = self.source.operation(action)
        required_scope = f"client_api:{self.source.alias}"
        if required_scope not in set(context.resource_scope) and "*" not in set(context.resource_scope):
            raise WriteAccessDenied("source resource unavailable")
        unknown = set(payload) - set(operation.allowed_fields)
        missing = set(operation.required_fields) - set(payload)
        if unknown:
            raise WriteOperationNotAllowed("payload contains fields outside configured write contract")
        if missing:
            raise WriteOperationNotAllowed("payload is missing configured required fields")
        auth_headers = self.auth_provider.headers(context=context, connector_id=self.connector_id)
        if not auth_headers:
            raise WriteAccessDenied("source authorization unavailable")
        headers = dict(auth_headers)
        headers["Idempotency-Key"] = idempotency_key
        url = self.source.base_url.rstrip("/") + operation.path
        try:
            response = self.transport.send_json(
                url,
                method=operation.method,
                headers=headers,
                payload=payload,
                timeout_seconds=self.timeout_seconds,
                max_response_bytes=self.max_response_bytes,
            )
        except WriteHTTPError as exc:
            if exc.status in {401, 403, 404}:
                raise WriteAccessDenied("source mutation denied") from exc
            if exc.status in {409, 422}:
                raise WriteOperationNotAllowed("source rejected mutation contract") from exc
            if exc.status == 429 or 500 <= exc.status <= 599:
                raise WriteUnavailable("source system unavailable") from exc
            raise WriteUnavailable("source mutation failed") from exc
        return self._receipt(operation, response)

    @staticmethod
    def _get(response: Mapping[str, Any], dotted: str) -> Any:
        current: Any = response
        for part in dotted.split("."):
            if not isinstance(current, Mapping) or part not in current:
                raise WriteSchemaInvalid("required source receipt field is absent")
            current = current[part]
        return current

    def _receipt(self, operation: WriteOperation, response: Mapping[str, Any]) -> ActionReceipt:
        record_id = self._get(response, operation.record_id_field)
        state = self._get(response, operation.state_field)
        tenant_id = self._get(response, operation.tenant_field)
        actor_id = self._get(response, operation.actor_field)
        idem = self._get(response, operation.idempotency_field)
        revision = None
        if operation.revision_field:
            raw = self._get(response, operation.revision_field)
            revision = str(raw)
        values = (record_id, state, tenant_id, actor_id, idem)
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise WriteSchemaInvalid("source receipt contains invalid identity/state fields")
        return ActionReceipt(
            action=operation.action,
            source_record_id=str(record_id),
            source_state=str(state),
            source_tenant_id=str(tenant_id),
            source_actor_id=str(actor_id),
            idempotency_key=str(idem),
            revision=revision,
        )
