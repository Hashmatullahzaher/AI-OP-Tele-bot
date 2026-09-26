"""Secure source-linked report artifacts for OS AI Core F6.

This milestone intentionally implements CSV first.  Native XLSX/PDF layout
requirements remain an owner decision (O-09).

Report IDs are opaque handles, not bearer authorization. Every download checks
the current tenant actor, an explicit report.download grant, the original source
capability/resource grants, expiry, file size and SHA-256 digest.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .agent import GroundedAnswer
from .contracts import ExecutionContext, JSONValue, PolicyDenied
from .storage import TenantSecurityStore

CSV_MIME = "text/csv; charset=utf-8"
_REPORT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")


class ReportError(RuntimeError):
    """Base class for sanitized report failures."""


class ReportAccessDenied(ReportError):
    """The caller cannot disclose the requested report."""


class ReportExpired(ReportError):
    """The report access period has expired."""


class ReportCorrupted(ReportError):
    """Stored report bytes no longer match their immutable metadata."""


class ReportSchemaInvalid(ReportError):
    """Grounded data cannot be represented without guessing."""


@dataclass(frozen=True)
class ReportArtifact:
    report_id: str
    tenant_id: str
    owner_actor_id: str
    filename: str
    mime_type: str
    byte_size: int
    sha256: str
    created_at_epoch: int
    expires_at_epoch: int
    source_capability: str
    source_scopes: tuple[str, ...]
    sources: tuple[Mapping[str, JSONValue], ...]
    semantics: Mapping[str, JSONValue]
    storage_key: str


@dataclass(frozen=True)
class ReportDownload:
    artifact: ReportArtifact
    content: bytes


class CSVReportStore:
    """Filesystem bytes + tenant-scoped metadata with authenticated retrieval."""

    def __init__(
        self,
        *,
        root: str | Path,
        security_store: TenantSecurityStore,
        max_csv_bytes: int = 10_000_000,
    ) -> None:
        if max_csv_bytes < 1 or max_csv_bytes > 100_000_000:
            raise ValueError("max_csv_bytes out of range")
        requested_root = Path(root)
        if requested_root.exists() and requested_root.is_symlink():
            raise ValueError("report root cannot be a symlink")
        requested_root.mkdir(parents=True, exist_ok=True)
        self.root = requested_root.resolve()
        self.security_store = security_store
        self.max_csv_bytes = max_csv_bytes

    def create_csv(
        self,
        *,
        context: ExecutionContext,
        answer: GroundedAnswer,
        filename: str,
        now_epoch: int,
        expires_at_epoch: int,
    ) -> ReportArtifact:
        if now_epoch < 0 or expires_at_epoch <= now_epoch:
            raise ValueError("report expiry must be after creation time")
        normalized_filename = _validate_filename(filename)
        if not answer.sources:
            raise ReportSchemaInvalid("source-linked report requires provenance")
        if not context.resource_scope:
            raise ReportAccessDenied("source scope is required for report creation")

        try:
            self.security_store.require_grant(
                context=context,
                capability="report.create",
                resource_scope="report:create",
            )
            for source_scope in context.resource_scope:
                self.security_store.require_grant(
                    context=context,
                    capability=answer.capability,
                    resource_scope=source_scope,
                )
        except PolicyDenied as exc:
            raise ReportAccessDenied("report creation denied") from exc

        content, semantics = _build_csv(answer.authoritative_data)
        if len(content) > self.max_csv_bytes:
            raise ReportSchemaInvalid("CSV report exceeds configured byte limit")

        report_id = secrets.token_urlsafe(24)
        storage_key = f"{report_id}.csv"
        final_path = self._path(storage_key)
        temp_key = f".{report_id}.{secrets.token_hex(8)}.tmp"
        temp_path = self._path(temp_key)
        digest = hashlib.sha256(content).hexdigest()
        sources = tuple(_source_manifest(source) for source in answer.sources)
        payload: dict[str, Any] = {
            "schema_version": "0.1",
            "report_id": report_id,
            "tenant_id": context.tenant_id,
            "owner_actor_id": context.actor_id,
            "filename": normalized_filename,
            "mime_type": CSV_MIME,
            "byte_size": len(content),
            "sha256": digest,
            "created_at_epoch": now_epoch,
            "expires_at_epoch": expires_at_epoch,
            "source_capability": answer.capability,
            "source_scopes": list(context.resource_scope),
            "sources": [dict(source) for source in sources],
            "semantics": dict(semantics),
            "storage_key": storage_key,
            "state": "ACTIVE",
            "csv_formula_protection": True,
        }

        self._atomic_write(temp_path, final_path, content)
        try:
            self.security_store.put_scoped(
                tenant_id=context.tenant_id,
                kind="report",
                object_id=report_id,
                payload=payload,
            )
        except Exception:
            final_path.unlink(missing_ok=True)
            raise

        self.security_store.append_audit(
            context=context,
            event="report.created",
            capability="report.create",
            resource_scope=f"report:{report_id}",
            result="OK",
            sensitive_payload={
                "format": "csv",
                "byte_size": len(content),
                "source_count": len(sources),
            },
        )
        return _artifact_from_payload(payload)

    def download_csv(
        self,
        *,
        context: ExecutionContext,
        owner_tenant_id: str,
        report_id: str,
        now_epoch: int,
    ) -> ReportDownload:
        if now_epoch < 0 or not _REPORT_ID_RE.fullmatch(report_id):
            raise ReportAccessDenied("report unavailable")
        try:
            payload = self.security_store.get_scoped(
                context=context,
                owner_tenant_id=owner_tenant_id,
                kind="report",
                object_id=report_id,
            )
            artifact = _artifact_from_payload(payload)
            if artifact.report_id != report_id or artifact.tenant_id != context.tenant_id:
                raise ReportAccessDenied("report unavailable")
            self.security_store.require_grant(
                context=context,
                capability="report.download",
                resource_scope=f"report:{report_id}",
            )
            for source_scope in artifact.source_scopes:
                self.security_store.require_grant(
                    context=context,
                    capability=artifact.source_capability,
                    resource_scope=source_scope,
                )
        except PolicyDenied as exc:
            raise ReportAccessDenied("report unavailable") from exc

        if now_epoch >= artifact.expires_at_epoch:
            self.security_store.append_audit(
                context=context,
                event="report.download",
                capability="report.download",
                resource_scope=f"report:{report_id}",
                result="EXPIRED",
                sensitive_payload={"format": "csv"},
            )
            raise ReportExpired("report expired")

        path = self._path(artifact.storage_key)
        try:
            content = path.read_bytes()
        except OSError as exc:
            self._audit_corrupt(context, report_id)
            raise ReportCorrupted("report artifact unavailable") from exc
        if len(content) != artifact.byte_size or not secrets.compare_digest(
            hashlib.sha256(content).hexdigest(),
            artifact.sha256,
        ):
            self._audit_corrupt(context, report_id)
            raise ReportCorrupted("report artifact integrity check failed")

        self.security_store.append_audit(
            context=context,
            event="report.download",
            capability="report.download",
            resource_scope=f"report:{report_id}",
            result="OK",
            sensitive_payload={"format": "csv", "byte_size": len(content)},
        )
        return ReportDownload(artifact=artifact, content=content)

    def _audit_corrupt(self, context: ExecutionContext, report_id: str) -> None:
        self.security_store.append_audit(
            context=context,
            event="report.download",
            capability="report.download",
            resource_scope=f"report:{report_id}",
            result="CORRUPT",
            sensitive_payload={"format": "csv"},
        )

    def _path(self, storage_key: str) -> Path:
        if not re.fullmatch(r"\.?[A-Za-z0-9_-]{20,160}(?:\.[A-Za-z0-9_-]{8,32}\.tmp|\.csv)", storage_key):
            raise ReportCorrupted("invalid report storage key")
        path = (self.root / storage_key).resolve()
        if path.parent != self.root:
            raise ReportCorrupted("report path escaped storage root")
        return path

    @staticmethod
    def _atomic_write(temp_path: Path, final_path: Path, content: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(temp_path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, final_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise


def _validate_filename(filename: str) -> str:
    normalized = unicodedata.normalize("NFC", filename.strip())
    if (
        not normalized
        or len(normalized) > 160
        or not normalized.casefold().endswith(".csv")
        or "/" in normalized
        or "\\" in normalized
        or normalized in {".", ".."}
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized)
    ):
        raise ValueError("invalid CSV filename")
    if len(normalized.encode("utf-8")) > 240:
        raise ValueError("CSV filename is too long")
    return normalized


def _source_manifest(source: Any) -> Mapping[str, JSONValue]:
    if not isinstance(source.source_id, str) or not isinstance(source.source_type, str):
        raise ReportSchemaInvalid("source provenance is invalid")
    locator = dict(source.locator)
    return {
        "source_id": source.source_id,
        "source_type": source.source_type,
        "revision": source.revision,
        "locator": cast(JSONValue, locator),
    }


def _build_csv(data: Mapping[str, JSONValue]) -> tuple[bytes, Mapping[str, JSONValue]]:
    kind = data.get("kind")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")

    if kind == "table":
        columns_raw = data.get("columns")
        rows_raw = data.get("rows")
        if not isinstance(columns_raw, list) or not isinstance(rows_raw, list):
            raise ReportSchemaInvalid("table report is missing rows/columns")
        columns = _string_list(columns_raw, "table columns")
        if not columns or len(columns) != len(set(columns)):
            raise ReportSchemaInvalid("table report columns are ambiguous")
        writer.writerow([_csv_safe(value) for value in columns])
        written = 0
        for row_raw in rows_raw:
            if not isinstance(row_raw, list):
                raise ReportSchemaInvalid("table report row is invalid")
            row = _string_list(row_raw, "table row")
            if len(row) != len(columns):
                raise ReportSchemaInvalid("table report row coverage is invalid")
            writer.writerow([_csv_safe(value) for value in row])
            written += 1
        declared = data.get("row_count")
        if not isinstance(declared, int) or isinstance(declared, bool) or declared != written:
            raise ReportSchemaInvalid("table report row_count does not match rows")
        semantics: dict[str, JSONValue] = {
            "kind": "table",
            "row_count": written,
            "column_count": len(columns),
        }

    elif kind == "count":
        count = data.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ReportSchemaInvalid("count report value is invalid")
        writer.writerow(["Metric", "Value"])
        writer.writerow(["Count", str(count)])
        semantics = {"kind": "count", "count": count}

    elif kind == "sum":
        column = data.get("column")
        amount = data.get("amount")
        currency = data.get("currency")
        matched_rows = data.get("matched_rows")
        if not isinstance(column, str) or not column:
            raise ReportSchemaInvalid("sum report column is invalid")
        if not isinstance(amount, str) or not _decimal_text(amount):
            raise ReportSchemaInvalid("sum report amount is invalid")
        if currency is not None and not isinstance(currency, str):
            raise ReportSchemaInvalid("sum report currency is invalid")
        if not isinstance(matched_rows, int) or isinstance(matched_rows, bool) or matched_rows < 0:
            raise ReportSchemaInvalid("sum report row coverage is invalid")
        writer.writerow(["Metric", "Value", "Currency", "MatchedRows"])
        writer.writerow(
            [
                _csv_safe(f"Sum:{column}"),
                amount,
                _csv_safe(currency or ""),
                str(matched_rows),
            ]
        )
        semantics = {
            "kind": "sum",
            "column": column,
            "amount": amount,
            "currency": currency,
            "matched_rows": matched_rows,
        }
    else:
        raise ReportSchemaInvalid("unsupported grounded report kind")

    # UTF-8 BOM keeps Dari/Unicode column text interoperable with common Excel imports.
    return ("\ufeff" + buffer.getvalue()).encode("utf-8"), semantics


def _string_list(values: list[JSONValue], label: str) -> list[str]:
    if not all(isinstance(value, str) for value in values):
        raise ReportSchemaInvalid(f"{label} must contain strings")
    return cast(list[str], values)


def _decimal_text(value: str) -> bool:
    return re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value) is not None


def _csv_safe(value: str) -> str:
    """Neutralize spreadsheet-formula injection without converting numeric text."""

    stripped = value.lstrip()
    if not stripped:
        return value
    if re.fullmatch(r"[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", stripped):
        return value
    if stripped[0] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + value
    return value


def _artifact_from_payload(payload: Mapping[str, Any]) -> ReportArtifact:
    try:
        if payload.get("schema_version") != "0.1" or payload.get("state") != "ACTIVE":
            raise ValueError("invalid metadata state")
        report_id = str(payload["report_id"])
        tenant_id = str(payload["tenant_id"])
        owner_actor_id = str(payload["owner_actor_id"])
        filename = str(payload["filename"])
        mime_type = str(payload["mime_type"])
        storage_key = str(payload["storage_key"])
        byte_size = int(payload["byte_size"])
        created_at_epoch = int(payload["created_at_epoch"])
        expires_at_epoch = int(payload["expires_at_epoch"])
        sha256 = str(payload["sha256"])
        source_capability = str(payload["source_capability"])
        source_scopes_raw = payload["source_scopes"]
        sources_raw = payload["sources"]
        semantics_raw = payload["semantics"]
        if (
            not _REPORT_ID_RE.fullmatch(report_id)
            or not tenant_id
            or not owner_actor_id
            or mime_type != CSV_MIME
            or byte_size < 0
            or len(sha256) != 64
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)
            or created_at_epoch < 0
            or expires_at_epoch <= created_at_epoch
            or not source_capability
            or not isinstance(source_scopes_raw, list)
            or not source_scopes_raw
            or not all(isinstance(item, str) and item for item in source_scopes_raw)
            or not isinstance(sources_raw, list)
            or not sources_raw
            or not all(isinstance(item, Mapping) for item in sources_raw)
            or not isinstance(semantics_raw, Mapping)
        ):
            raise ValueError("invalid report metadata")
        _validate_filename(filename)
        sources = tuple(cast(Mapping[str, JSONValue], dict(item)) for item in sources_raw)
        semantics = cast(Mapping[str, JSONValue], dict(semantics_raw))
        return ReportArtifact(
            report_id=report_id,
            tenant_id=tenant_id,
            owner_actor_id=owner_actor_id,
            filename=filename,
            mime_type=mime_type,
            byte_size=byte_size,
            sha256=sha256,
            created_at_epoch=created_at_epoch,
            expires_at_epoch=expires_at_epoch,
            source_capability=source_capability,
            source_scopes=tuple(cast(list[str], source_scopes_raw)),
            sources=sources,
            semantics=semantics,
            storage_key=storage_key,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReportCorrupted("report metadata is invalid") from exc
