"""Read-only connector for CSV/XLSX files on the same machine.

Used for local testing and for companies that keep data in files instead of
Google Drive. Only operator-allowlisted files (by alias) can be read; the model
never supplies a path.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from ..contracts import CapabilityManifest, ExecutionContext, JSONValue, SourceProvenance, ToolResult
from .google_drive import (
    DriveAccessDenied,
    DriveResourceNotAllowed,
    DriveSchemaAmbiguous,
    DriveUnavailable,
    normalize_cell,
    parse_xlsx_table,
)

LOCAL_CAPABILITY = "local.table.read"


@dataclass(frozen=True)
class LocalTable:
    alias: str
    sheet_name: str | None = None  # XLSX worksheet; ignored for CSV

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", self.alias):
            raise ValueError("invalid table alias")


@dataclass(frozen=True)
class LocalResource:
    alias: str
    path: Path
    kind: str  # "csv" or "xlsx_file"
    tables: tuple[LocalTable, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", self.alias):
            raise ValueError("invalid resource alias")
        if self.kind not in {"csv", "xlsx_file"}:
            raise ValueError("kind must be csv or xlsx_file")
        if self.kind == "csv" and len(self.tables) != 1:
            raise ValueError("a csv resource has exactly one table")
        if self.kind == "xlsx_file" and any(not t.sheet_name for t in self.tables):
            raise ValueError("xlsx_file tables need sheet_name")

    def table(self, alias: str) -> LocalTable:
        for table in self.tables:
            if table.alias == alias:
                return table
        raise DriveResourceNotAllowed("table is not allowlisted")


class LocalFileConnector:
    connector_type = "local"

    def __init__(
        self,
        *,
        resources: Mapping[str, LocalResource],
        max_bytes: int = 20_000_000,
        max_rows: int = 20_000,
        max_columns: int = 100,
    ) -> None:
        self.resources = dict(resources)
        self.max_bytes = max_bytes
        self.max_rows = max_rows
        self.max_columns = max_columns

    def invoke(
        self,
        *,
        capability: str,
        arguments: Mapping[str, JSONValue],
        context: ExecutionContext,
    ) -> ToolResult:
        if capability != LOCAL_CAPABILITY:
            raise DriveResourceNotAllowed("local capability is not supported")
        resource = self.resources.get(str(arguments.get("resource_alias", "")))
        if resource is None:
            raise DriveResourceNotAllowed("resource is not allowlisted")
        scopes = set(context.resource_scope)
        if f"local:{resource.alias}" not in scopes and "*" not in scopes:
            raise DriveAccessDenied("source resource unavailable")
        table = resource.table(str(arguments.get("table_alias", "")))
        try:
            stat = resource.path.stat()
            if stat.st_size > self.max_bytes:
                raise DriveSchemaAmbiguous("file exceeds configured size limit")
            payload = resource.path.read_bytes()
        except OSError as exc:
            raise DriveUnavailable("local file unavailable") from exc
        if resource.kind == "csv":
            values = self._read_csv(payload)
        else:
            values = parse_xlsx_table(
                payload,
                sheet_name=cast(str, table.sheet_name),
                max_rows=self.max_rows + 1,
                max_columns=self.max_columns,
                max_uncompressed_bytes=self.max_bytes * 5,
            )
        columns, rows = self._normalize(values)
        revision = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(timespec="seconds")
        data: dict[str, JSONValue] = {
            "resource_alias": resource.alias,
            "table_alias": table.alias,
            "columns": cast(JSONValue, columns),
            "rows": cast(JSONValue, rows),
            "row_count": len(rows),
            "revision": revision,
            "source_kind": resource.kind,
        }
        provenance = SourceProvenance.observed(
            source_id=resource.alias,
            source_type=resource.kind,
            revision=revision,
            locator={"table_alias": table.alias, "sheet_name": table.sheet_name or resource.path.name},
        )
        return ToolResult(data=data, provenance=(provenance,))

    @staticmethod
    def _read_csv(payload: bytes) -> list[list[object]]:
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DriveSchemaAmbiguous("csv must be UTF-8") from exc
        return [list(row) for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]

    def _normalize(self, values: list[list[object]]) -> tuple[list[str], list[list[str]]]:
        if not values:
            raise DriveSchemaAmbiguous("configured table is empty")
        headers = [normalize_cell(value).strip() for value in values[0]]
        if not headers or len(headers) > self.max_columns or any(not h for h in headers):
            raise DriveSchemaAmbiguous("configured table has invalid headers")
        if len({h.casefold() for h in headers}) != len(headers):
            raise DriveSchemaAmbiguous("configured table has duplicate headers")
        rows: list[list[str]] = []
        for raw_row in values[1:]:
            if len(rows) >= self.max_rows:
                raise DriveSchemaAmbiguous("table row count exceeded configured limit")
            if len(raw_row) > len(headers):
                raise DriveSchemaAmbiguous("row contains more cells than declared headers")
            row = [normalize_cell(value) for value in raw_row]
            row.extend([""] * (len(headers) - len(row)))
            rows.append(row)
        return headers, rows


def local_table_manifest() -> CapabilityManifest:
    return CapabilityManifest.from_dict(
        {
            "schema_version": "0.1",
            "connector_type": "local",
            "capability": LOCAL_CAPABILITY,
            "action": "READ",
            "input_schema": {
                "type": "object",
                "properties": {
                    "resource_alias": {"type": "string", "minLength": 3, "maxLength": 64},
                    "table_alias": {"type": "string", "minLength": 2, "maxLength": 64},
                },
                "required": ["resource_alias", "table_alias"],
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "resource_alias": {"type": "string"},
                    "table_alias": {"type": "string"},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                    "row_count": {"type": "integer", "minimum": 0},
                    "revision": {"type": "string"},
                    "source_kind": {"type": "string", "enum": ["csv", "xlsx_file"]},
                },
                "required": [
                    "resource_alias",
                    "table_alias",
                    "columns",
                    "rows",
                    "row_count",
                    "revision",
                    "source_kind",
                ],
                "additionalProperties": False,
            },
            "required_scopes": ["local:read"],
            "resource_allowlist_ref": "policyref:local-resource-aliases",
            "mutates": False,
            "timeout_seconds": 20,
        }
    )
