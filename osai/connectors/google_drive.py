"""Read-only Google Drive/Sheets connector for OS AI Core F3.

Security properties:
- callers address only server-configured aliases, never raw Drive IDs or URLs;
- every invocation re-fetches Drive metadata using the current OAuth bearer token;
- an expected parent folder and MIME type are enforced before disclosure;
- Sheets reads use server-configured table aliases/ranges only;
- XLSX downloads are bounded and parsed without executing formulas/macros;
- source metadata is returned as provenance for downstream audit/reporting.

OAuth consent, refresh-token custody and concrete secret storage are deployment
concerns.  This module consumes an injected access-token provider and does not
persist OAuth credentials itself.
"""

from __future__ import annotations

import io
import json
import posixpath
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn, Protocol
from xml.etree import ElementTree as ET

from ..contracts import CapabilityManifest, ExecutionContext, JSONValue, SourceProvenance, ToolResult

GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_DRIVE_API_HOST = "www.googleapis.com"
_SHEETS_API_HOST = "sheets.googleapis.com"


class DriveConnectorError(RuntimeError):
    """Base class for sanitized connector failures."""


class DriveAccessDenied(DriveConnectorError):
    """The current source credential cannot disclose the configured resource."""


class DriveResourceNotAllowed(DriveConnectorError):
    """A user/model attempted to address a resource outside the configured allowlist."""


class DriveUnavailable(DriveConnectorError):
    """Google is unavailable or returned a non-authorization transport failure."""


class DriveSchemaAmbiguous(DriveConnectorError):
    """The source table cannot be normalized without guessing semantics."""


class DriveLimitExceeded(DriveConnectorError):
    """A bounded read exceeded an explicit safety limit."""


class GoogleHTTPError(RuntimeError):
    def __init__(self, status: int, message: str = "google request failed") -> None:
        super().__init__(message)
        self.status = status


class AccessTokenProvider(Protocol):
    def bearer_token(self, *, context: ExecutionContext, connector_id: str) -> str:
        """Return a short-lived bearer token from a deployment secret boundary."""


class GoogleTransport(Protocol):
    def get_json(self, url: str, *, bearer_token: str, timeout_seconds: int) -> Mapping[str, Any]:
        """GET one Google JSON API resource."""

    def get_bytes(self, url: str, *, bearer_token: str, timeout_seconds: int, max_bytes: int) -> bytes:
        """GET one bounded binary Google resource."""


class UrllibGoogleTransport:
    """Small production HTTP transport using the standard library.

    Only Google Drive/Sheets API hosts are accepted. Redirects are rejected so a
    compromised/misconfigured endpoint cannot bounce credentials elsewhere.
    """

    _ALLOWED_HOSTS = frozenset({_DRIVE_API_HOST, _SHEETS_API_HOST})

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
            raise GoogleHTTPError(code, "redirect refused")

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(self._NoRedirect())

    def _request(self, url: str, bearer_token: str) -> urllib.request.Request:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in self._ALLOWED_HOSTS:
            raise DriveResourceNotAllowed("connector attempted a non-allowlisted Google host")
        return urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "Accept": "application/json",
                "User-Agent": "os-ai-core/0.1",
            },
            method="GET",
        )

    def get_json(self, url: str, *, bearer_token: str, timeout_seconds: int) -> Mapping[str, Any]:
        request = self._request(url, bearer_token)
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                payload = response.read(2_000_001)
        except urllib.error.HTTPError as exc:
            raise GoogleHTTPError(exc.code) from exc
        except urllib.error.URLError as exc:
            raise DriveUnavailable("google source unavailable") from exc
        if len(payload) > 2_000_000:
            raise DriveLimitExceeded("google metadata response exceeded limit")
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DriveUnavailable("google returned invalid metadata") from exc
        if not isinstance(decoded, Mapping):
            raise DriveUnavailable("google returned an unexpected metadata shape")
        return decoded

    def get_bytes(self, url: str, *, bearer_token: str, timeout_seconds: int, max_bytes: int) -> bytes:
        request = self._request(url, bearer_token)
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                payload = response.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise GoogleHTTPError(exc.code) from exc
        except urllib.error.URLError as exc:
            raise DriveUnavailable("google source unavailable") from exc
        if len(payload) > max_bytes:
            raise DriveLimitExceeded("xlsx download exceeded configured byte limit")
        return payload


@dataclass(frozen=True)
class DriveTable:
    alias: str
    sheet_name: str
    a1_range: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", self.alias):
            raise ValueError("invalid table alias")
        if not self.sheet_name.strip():
            raise ValueError("sheet_name is required")
        if self.a1_range is not None and not self.a1_range.strip():
            raise ValueError("a1_range cannot be blank")


@dataclass(frozen=True)
class DriveResource:
    alias: str
    file_id: str
    kind: str
    expected_parent_id: str
    tables: tuple[DriveTable, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", self.alias):
            raise ValueError("invalid resource alias")
        if self.kind not in {"google_sheet", "xlsx"}:
            raise ValueError("kind must be google_sheet or xlsx")
        if not self.file_id or "/" in self.file_id or ":" in self.file_id:
            raise ValueError("file_id must be an opaque Drive ID, not a URL")
        if not self.expected_parent_id:
            raise ValueError("expected_parent_id is required")
        table_aliases = [table.alias for table in self.tables]
        if len(table_aliases) != len(set(table_aliases)):
            raise ValueError("duplicate table aliases are not allowed")
        if self.kind == "xlsx" and any(table.a1_range is not None for table in self.tables):
            raise ValueError("xlsx tables use whole configured sheets, not caller-selected ranges")

    def table(self, alias: str) -> DriveTable:
        for table in self.tables:
            if table.alias == alias:
                return table
        raise DriveResourceNotAllowed("table is not allowlisted")


@dataclass(frozen=True)
class StaticTokenProvider:
    """Test/development token provider. Never commit a real token."""

    token: str

    def bearer_token(self, *, context: ExecutionContext, connector_id: str) -> str:
        if not self.token:
            raise DriveAccessDenied("source credential unavailable")
        return self.token


class GoogleDriveConnector:
    connector_type = "drive"

    def __init__(
        self,
        *,
        connector_id: str,
        resources: Mapping[str, DriveResource],
        token_provider: AccessTokenProvider,
        transport: GoogleTransport | None = None,
        timeout_seconds: int = 20,
        max_download_bytes: int = 8_000_000,
        max_rows: int = 5_000,
        max_columns: int = 100,
    ) -> None:
        if not connector_id:
            raise ValueError("connector_id is required")
        if timeout_seconds < 1 or timeout_seconds > 60:
            raise ValueError("timeout_seconds out of range")
        if max_download_bytes < 1 or max_download_bytes > 25_000_000:
            raise ValueError("max_download_bytes out of range")
        if max_rows < 1 or max_rows > 20_000 or max_columns < 1 or max_columns > 250:
            raise ValueError("table limits out of range")
        if not resources:
            raise ValueError("at least one resource is required")
        if set(resources) != {resource.alias for resource in resources.values()}:
            raise ValueError("resource map key must match resource alias")
        self.connector_id = connector_id
        self.resources = dict(resources)
        self.token_provider = token_provider
        self.transport = transport or UrllibGoogleTransport()
        self.timeout_seconds = timeout_seconds
        self.max_download_bytes = max_download_bytes
        self.max_rows = max_rows
        self.max_columns = max_columns

    def invoke(
        self,
        *,
        capability: str,
        arguments: Mapping[str, JSONValue],
        context: ExecutionContext,
    ) -> ToolResult:
        if capability != "drive.table.read":
            raise DriveResourceNotAllowed("drive capability is not supported")
        resource_alias = str(arguments.get("resource_alias", ""))
        table_alias = str(arguments.get("table_alias", ""))
        resource = self.resources.get(resource_alias)
        if resource is None:
            raise DriveResourceNotAllowed("resource is not allowlisted")
        self._require_context_scope(context, resource_alias)
        table = resource.table(table_alias)
        token = self.token_provider.bearer_token(context=context, connector_id=self.connector_id)
        if not token or any(ch.isspace() for ch in token):
            raise DriveAccessDenied("source credential unavailable")
        metadata = self._metadata(resource, token)
        modified_time = metadata.get("modifiedTime")
        if not isinstance(modified_time, str) or not modified_time.strip():
            raise DriveUnavailable("source revision metadata unavailable")
        revision = modified_time
        if resource.kind == "google_sheet":
            values = self._read_google_sheet(resource, table, token)
            source_type = "google_sheet"
        else:
            values = self._read_xlsx(resource, table, token)
            source_type = "xlsx"
        columns, rows = self._normalize_table(values)
        data: dict[str, JSONValue] = {
            "resource_alias": resource.alias,
            "table_alias": table.alias,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "revision": revision,
            "source_kind": source_type,
        }
        provenance = SourceProvenance.observed(
            source_id=resource.alias,
            source_type=source_type,
            revision=revision,
            locator={
                "table_alias": table.alias,
                "sheet_name": table.sheet_name,
                "range": table.a1_range or "configured-sheet",
            },
        )
        return ToolResult(data=data, provenance=(provenance,))

    def _require_context_scope(self, context: ExecutionContext, resource_alias: str) -> None:
        required = f"drive:{resource_alias}"
        scopes = set(context.resource_scope)
        if required not in scopes and "*" not in scopes:
            raise DriveAccessDenied("source resource unavailable")

    def _metadata(self, resource: DriveResource, token: str) -> Mapping[str, Any]:
        fields = "id,name,mimeType,modifiedTime,parents,size,capabilities(canDownload)"
        query = urllib.parse.urlencode({"fields": fields, "supportsAllDrives": "true"})
        file_id = urllib.parse.quote(resource.file_id, safe="")
        url = f"https://{_DRIVE_API_HOST}/drive/v3/files/{file_id}?{query}"
        metadata = self._google_json(url, token)
        expected_mime = GOOGLE_SHEET_MIME if resource.kind == "google_sheet" else XLSX_MIME
        if metadata.get("id") != resource.file_id or metadata.get("mimeType") != expected_mime:
            raise DriveAccessDenied("source resource unavailable")
        parents = metadata.get("parents")
        if not isinstance(parents, list) or resource.expected_parent_id not in parents:
            raise DriveAccessDenied("source resource unavailable")
        if resource.kind == "xlsx":
            capabilities = metadata.get("capabilities")
            if isinstance(capabilities, Mapping) and capabilities.get("canDownload") is False:
                raise DriveAccessDenied("source resource unavailable")
            size = metadata.get("size")
            if size is not None:
                try:
                    if int(str(size)) > self.max_download_bytes:
                        raise DriveLimitExceeded("xlsx file exceeds configured byte limit")
                except ValueError as exc:
                    raise DriveUnavailable("invalid source size metadata") from exc
        return metadata

    def _read_google_sheet(self, resource: DriveResource, table: DriveTable, token: str) -> list[list[object]]:
        escaped_sheet_name = table.sheet_name.replace("'", "''")
        range_value = table.a1_range or f"'{escaped_sheet_name}'"
        encoded_range = urllib.parse.quote(range_value, safe="")
        file_id = urllib.parse.quote(resource.file_id, safe="")
        query = urllib.parse.urlencode(
            {"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "FORMATTED_STRING"}
        )
        url = (
            f"https://{_SHEETS_API_HOST}/v4/spreadsheets/{file_id}/values/{encoded_range}?{query}"
        )
        payload = self._google_json(url, token)
        values = payload.get("values", [])
        if not isinstance(values, list) or not all(isinstance(row, list) for row in values):
            raise DriveSchemaAmbiguous("sheet returned an unexpected value matrix")
        if len(values) > self.max_rows + 1:
            raise DriveLimitExceeded("sheet row count exceeded configured limit")
        if any(len(row) > self.max_columns for row in values):
            raise DriveLimitExceeded("sheet column count exceeded configured limit")
        return [list(row) for row in values]

    def _read_xlsx(self, resource: DriveResource, table: DriveTable, token: str) -> list[list[object]]:
        file_id = urllib.parse.quote(resource.file_id, safe="")
        query = urllib.parse.urlencode({"alt": "media", "supportsAllDrives": "true"})
        url = f"https://{_DRIVE_API_HOST}/drive/v3/files/{file_id}?{query}"
        try:
            payload = self.transport.get_bytes(
                url,
                bearer_token=token,
                timeout_seconds=self.timeout_seconds,
                max_bytes=self.max_download_bytes,
            )
        except GoogleHTTPError as exc:
            self._raise_google_error(exc)
        return parse_xlsx_table(
            payload,
            sheet_name=table.sheet_name,
            max_rows=self.max_rows + 1,
            max_columns=self.max_columns,
            max_uncompressed_bytes=max(self.max_download_bytes * 8, 1_000_000),
        )

    def _google_json(self, url: str, token: str) -> Mapping[str, Any]:
        try:
            return self.transport.get_json(
                url,
                bearer_token=token,
                timeout_seconds=self.timeout_seconds,
            )
        except GoogleHTTPError as exc:
            self._raise_google_error(exc)

    @staticmethod
    def _raise_google_error(exc: GoogleHTTPError) -> NoReturn:
        if exc.status in {401, 403, 404}:
            raise DriveAccessDenied("source resource unavailable") from exc
        if exc.status == 429 or 500 <= exc.status <= 599:
            raise DriveUnavailable("google source unavailable") from exc
        raise DriveUnavailable("google source request failed") from exc

    def _normalize_table(self, values: list[list[object]]) -> tuple[list[str], list[list[str]]]:
        if not values:
            raise DriveSchemaAmbiguous("configured table is empty")
        raw_headers = values[0]
        if not raw_headers or len(raw_headers) > self.max_columns:
            raise DriveSchemaAmbiguous("configured table has invalid headers")
        headers = [normalize_cell(value) for value in raw_headers]
        if any(not header.strip() for header in headers):
            raise DriveSchemaAmbiguous("configured table has a blank header")
        folded = [header.casefold().strip() for header in headers]
        if len(folded) != len(set(folded)):
            raise DriveSchemaAmbiguous("configured table has duplicate headers")
        rows: list[list[str]] = []
        for raw_row in values[1:]:
            if len(rows) >= self.max_rows:
                raise DriveLimitExceeded("table row count exceeded configured limit")
            if len(raw_row) > len(headers):
                raise DriveSchemaAmbiguous("row contains more cells than declared headers")
            normalized = [normalize_cell(value) for value in raw_row]
            normalized.extend([""] * (len(headers) - len(normalized)))
            rows.append(normalized)
        return headers, rows


def drive_table_manifest() -> CapabilityManifest:
    """Canonical F3 read-only capability contract."""

    return CapabilityManifest.from_dict(
        {
            "schema_version": "0.1",
            "connector_type": "drive",
            "capability": "drive.table.read",
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
                    "rows": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                    },
                    "row_count": {"type": "integer", "minimum": 0},
                    "revision": {"type": "string"},
                    "source_kind": {"type": "string", "enum": ["google_sheet", "xlsx"]},
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
            "required_scopes": ["drive:read"],
            "resource_allowlist_ref": "policyref:drive-resource-aliases",
            "mutates": False,
            "timeout_seconds": 20,
        }
    )


def normalize_cell(value: object) -> str:
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
    return str(value)


def parse_xlsx_table(
    payload: bytes,
    *,
    sheet_name: str,
    max_rows: int,
    max_columns: int,
    max_uncompressed_bytes: int,
) -> list[list[object]]:
    """Parse one configured XLSX worksheet with strict archive/XML limits.

    Macro-enabled files are not accepted by the connector MIME contract. Formula
    text is never evaluated; when a formula cell has a cached value, only that
    cached value is returned.
    """

    if not payload.startswith(b"PK"):
        raise DriveSchemaAmbiguous("source is not an xlsx archive")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            if len(infos) > 1_000:
                raise DriveLimitExceeded("xlsx archive contains too many entries")
            total_uncompressed = sum(info.file_size for info in infos)
            if total_uncompressed > max_uncompressed_bytes:
                raise DriveLimitExceeded("xlsx archive exceeds uncompressed-size limit")
            names = {info.filename for info in infos}
            if len(names) != len(infos):
                raise DriveSchemaAmbiguous("xlsx archive contains duplicate paths")
            if any(name.startswith("/") or ".." in name.split("/") for name in names):
                raise DriveSchemaAmbiguous("xlsx archive contains unsafe paths")
            required = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
            if not required.issubset(names):
                raise DriveSchemaAmbiguous("xlsx workbook metadata is incomplete")
            workbook = _safe_xml(archive.read("xl/workbook.xml"))
            rels = _safe_xml(archive.read("xl/_rels/workbook.xml.rels"))
            sheet_path = _resolve_sheet_path(workbook, rels, sheet_name)
            if sheet_path not in names:
                raise DriveSchemaAmbiguous("configured worksheet is missing")
            shared_strings = _shared_strings(archive, names)
            worksheet = _safe_xml(archive.read(sheet_path))
            return _worksheet_values(
                worksheet,
                shared_strings=shared_strings,
                max_rows=max_rows,
                max_columns=max_columns,
            )
    except zipfile.BadZipFile as exc:
        raise DriveSchemaAmbiguous("source is not a valid xlsx archive") from exc


def _safe_xml(payload: bytes) -> ET.Element:
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise DriveSchemaAmbiguous("xlsx XML declarations are not allowed")
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise DriveSchemaAmbiguous("xlsx contains invalid XML") from exc


def _resolve_sheet_path(workbook: ET.Element, rels: ET.Element, sheet_name: str) -> str:
    ns = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    rel_map = {}
    for rel in rels.findall("pkg:Relationship", ns):
        rel_type = rel.attrib.get("Type", "")
        if rel_type.endswith("/worksheet"):
            rel_map[rel.attrib.get("Id", "")] = rel.attrib.get("Target", "")
    for sheet in workbook.findall("main:sheets/main:sheet", ns):
        if sheet.attrib.get("name") != sheet_name:
            continue
        rel_id = sheet.attrib.get(f"{{{ns['r']}}}id")
        target = rel_map.get(rel_id or "")
        if not target:
            raise DriveSchemaAmbiguous("configured worksheet relationship is missing")
        normalized = posixpath.normpath(posixpath.join("xl", target))
        if normalized.startswith("../") or not normalized.startswith("xl/"):
            raise DriveSchemaAmbiguous("configured worksheet path is invalid")
        return normalized
    raise DriveSchemaAmbiguous("configured worksheet is missing")


def _shared_strings(archive: zipfile.ZipFile, names: set[str]) -> list[str]:
    path = "xl/sharedStrings.xml"
    if path not in names:
        return []
    root = _safe_xml(archive.read(path))
    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values: list[str] = []
    for item in root.findall("main:si", ns):
        parts = [node.text or "" for node in item.iter(f"{{{ns['main']}}}t")]
        values.append("".join(parts))
        if len(values) > 200_000:
            raise DriveLimitExceeded("xlsx shared-string count exceeded limit")
    return values


def _worksheet_values(
    root: ET.Element,
    *,
    shared_strings: list[str],
    max_rows: int,
    max_columns: int,
) -> list[list[object]]:
    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    output: list[list[object]] = []
    for row in root.findall(".//main:sheetData/main:row", ns):
        if len(output) >= max_rows:
            raise DriveLimitExceeded("xlsx row count exceeded configured limit")
        cells: dict[int, object] = {}
        for cell in row.findall("main:c", ns):
            ref = cell.attrib.get("r", "")
            index = _column_index(ref)
            if index >= max_columns:
                raise DriveLimitExceeded("xlsx column count exceeded configured limit")
            cells[index] = _xlsx_cell_value(cell, shared_strings, ns)
        if not cells:
            output.append([])
            continue
        width = max(cells) + 1
        output.append([cells.get(index, "") for index in range(width)])
    return output


def _column_index(cell_ref: str) -> int:
    match = re.match(r"^([A-Z]+)", cell_ref.upper())
    if not match:
        raise DriveSchemaAmbiguous("xlsx cell reference is invalid")
    value = 0
    for char in match.group(1):
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1


def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str], ns: Mapping[str, str]) -> object:
    cell_type = cell.attrib.get("t")
    value_node = cell.find("main:v", ns)
    if cell_type == "inlineStr":
        parts = [node.text or "" for node in cell.iter(f"{{{ns['main']}}}t")]
        return "".join(parts)
    raw = value_node.text if value_node is not None and value_node.text is not None else ""
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError) as exc:
            raise DriveSchemaAmbiguous("xlsx shared-string index is invalid") from exc
    if cell_type == "b":
        return raw == "1"
    if cell_type in {"str", "e"}:
        return raw
    if raw == "":
        return ""
    try:
        number = float(raw)
    except ValueError:
        return raw
    return int(number) if number.is_integer() else number
