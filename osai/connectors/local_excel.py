"""Local XLSX sandbox connector and source adapter for Windows-first UAT.

This module intentionally treats Excel as a controlled pilot data source, not as
a production database. Paths and sheet names are server-configured; callers do
not supply arbitrary filesystem paths. Writes are serialized in-process and the
workbook is replaced atomically.

The workbook stores only plain values. Formula text from user-provided fields is
written as inline string data and is never executed by OS AI Core.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from ..actions import ActionReceipt
from ..contracts import (
    CapabilityManifest,
    ExecutionContext,
    JSONValue,
    SourceProvenance,
    ToolResult,
)
from .google_drive import DriveLimitExceeded, DriveSchemaAmbiguous, parse_xlsx_table


class LocalExcelError(RuntimeError):
    """Base class for sanitized local Excel UAT failures."""


class LocalExcelAccessDenied(LocalExcelError):
    """The actor/resource/path is not authorized for this Excel source."""


class LocalExcelRejected(LocalExcelError):
    """A deterministic source-domain rule rejected the requested mutation."""


class LocalExcelConflict(LocalExcelError):
    """An idempotency key conflicts with an existing source mutation."""


class LocalExcelUnavailable(LocalExcelError):
    """The workbook cannot currently be opened or atomically replaced."""


_HEADERS: dict[str, tuple[str, ...]] = {
    "Customers": (
        "CustomerID",
        "Name",
        "CustomerType",
        "Phone",
        "Email",
        "Address",
        "Status",
        "Revision",
        "CreatedAt",
        "UpdatedAt",
    ),
    "ProcurementRequests": (
        "PRID",
        "Requester",
        "Department",
        "Currency",
        "Justification",
        "EstimatedTotal",
        "Status",
        "Revision",
        "CreatedAt",
    ),
    "ProcurementItems": (
        "PRID",
        "LineNo",
        "Description",
        "Quantity",
        "UnitEstimatedCost",
        "LineTotal",
    ),
    "DraftVouchers": (
        "VoucherID",
        "VoucherDate",
        "Currency",
        "Description",
        "TotalDebit",
        "TotalCredit",
        "Status",
        "Revision",
        "CreatedAt",
    ),
    "VoucherEntries": (
        "VoucherID",
        "LineNo",
        "AccountCode",
        "Debit",
        "Credit",
        "Memo",
    ),
    "SystemJournal": (
        "IdempotencyKey",
        "Action",
        "TenantID",
        "ActorID",
        "PayloadDigest",
        "RecordID",
        "State",
        "Revision",
        "CreatedAt",
    ),
}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _payload_digest(action: str, payload: Mapping[str, JSONValue]) -> str:
    material = json.dumps(
        {"action": action, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _decimal(value: object, *, field: str, positive: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LocalExcelRejected(f"{field} is not a valid decimal") from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed == 0):
        raise LocalExcelRejected(f"{field} is invalid")
    return parsed.quantize(Decimal("0.01"))


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


@dataclass(frozen=True)
class ExcelTable:
    alias: str
    sheet_name: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", self.alias):
            raise ValueError("invalid Excel table alias")
        if self.sheet_name not in _HEADERS:
            raise ValueError("unsupported Excel sandbox sheet")


@dataclass(frozen=True)
class LocalExcelResource:
    alias: str
    workbook_path: str
    tables: tuple[ExcelTable, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", self.alias):
            raise ValueError("invalid Excel resource alias")
        path = Path(self.workbook_path)
        if path.suffix.casefold() != ".xlsx":
            raise ValueError("Excel sandbox path must end in .xlsx")
        if not path.is_absolute():
            raise ValueError("Excel sandbox path must be absolute")
        aliases = [table.alias for table in self.tables]
        if len(aliases) != len(set(aliases)):
            raise ValueError("duplicate Excel table aliases")
        if not aliases:
            raise ValueError("at least one Excel table alias is required")

    def table(self, alias: str) -> ExcelTable:
        for table in self.tables:
            if table.alias == alias:
                return table
        raise LocalExcelAccessDenied("Excel table is not allowlisted")


class LocalExcelConnector:
    """Read-only typed local XLSX connector for UAT."""

    connector_type = "local_excel"

    def __init__(
        self,
        *,
        resource: LocalExcelResource,
        max_bytes: int = 8_000_000,
        max_rows: int = 5_000,
        max_columns: int = 100,
    ) -> None:
        if max_bytes < 1 or max_bytes > 25_000_000:
            raise ValueError("max_bytes out of range")
        if max_rows < 1 or max_rows > 20_000 or max_columns < 1 or max_columns > 250:
            raise ValueError("table limits out of range")
        self.resource = resource
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
        if capability != "excel.table.read":
            raise LocalExcelAccessDenied("Excel capability is not supported")
        resource_alias = str(arguments.get("resource_alias", ""))
        table_alias = str(arguments.get("table_alias", ""))
        if resource_alias != self.resource.alias:
            raise LocalExcelAccessDenied("Excel resource is not allowlisted")
        required_scope = f"excel:{self.resource.alias}"
        scopes = set(context.resource_scope)
        if required_scope not in scopes and "*" not in scopes:
            raise LocalExcelAccessDenied("Excel resource unavailable")
        table = self.resource.table(table_alias)
        path = Path(self.resource.workbook_path)
        try:
            stat = path.stat()
            if stat.st_size > self.max_bytes:
                raise DriveLimitExceeded("xlsx file exceeds configured byte limit")
            payload = path.read_bytes()
        except OSError as exc:
            raise LocalExcelUnavailable("Excel workbook unavailable") from exc
        revision = hashlib.sha256(payload).hexdigest()
        values = parse_xlsx_table(
            payload,
            sheet_name=table.sheet_name,
            max_rows=self.max_rows + 1,
            max_columns=self.max_columns,
            max_uncompressed_bytes=max(self.max_bytes * 8, 1_000_000),
        )
        if not values:
            raise DriveSchemaAmbiguous("configured Excel table is empty")
        headers = [_safe_text(v) for v in values[0]]
        folded = [v.casefold().strip() for v in headers]
        if any(not v for v in folded) or len(folded) != len(set(folded)):
            raise DriveSchemaAmbiguous("configured Excel headers are ambiguous")
        rows: list[list[str]] = []
        for raw in values[1:]:
            if len(raw) > len(headers):
                raise DriveSchemaAmbiguous("Excel row exceeds declared headers")
            row = [_safe_text(v) for v in raw]
            row.extend([""] * (len(headers) - len(row)))
            rows.append(row)
        data: dict[str, JSONValue] = {
            "resource_alias": self.resource.alias,
            "table_alias": table.alias,
            "columns": headers,
            "rows": rows,
            "row_count": len(rows),
            "revision": revision,
            "source_kind": "local_xlsx",
        }
        provenance = SourceProvenance.observed(
            source_id=self.resource.alias,
            source_type="local_xlsx",
            revision=revision,
            locator={"table_alias": table.alias, "sheet_name": table.sheet_name},
        )
        return ToolResult(data=data, provenance=(provenance,))


def excel_table_manifest() -> CapabilityManifest:
    return CapabilityManifest.from_dict(
        {
            "schema_version": "0.1",
            "connector_type": "local_excel",
            "capability": "excel.table.read",
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
                    "source_kind": {"type": "string", "enum": ["local_xlsx"]},
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
            "required_scopes": ["excel:read"],
            "resource_allowlist_ref": "policyref:local-excel",
            "mutates": False,
            "timeout_seconds": 10,
        }
    )


class ExcelSandboxWriteAdapter:
    """Use one local XLSX workbook as a deterministic fake customer system."""

    _lock = threading.RLock()

    def __init__(self, *, workbook_path: str | Path, source_alias: str) -> None:
        self.path = Path(workbook_path)
        if not self.path.is_absolute() or self.path.suffix.casefold() != ".xlsx":
            raise ValueError("workbook_path must be an absolute .xlsx path")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", source_alias):
            raise ValueError("invalid source_alias")
        self.source_alias = source_alias

    def execute(
        self,
        *,
        action: str,
        payload: Mapping[str, JSONValue],
        context: ExecutionContext,
        idempotency_key: str,
    ) -> ActionReceipt:
        required_scope = f"client_api:{self.source_alias}"
        scopes = set(context.resource_scope)
        if required_scope not in scopes and "*" not in scopes:
            raise LocalExcelAccessDenied("Excel source unavailable")
        if action not in {
            "customer.create",
            "customer.update",
            "procurement.request.create",
            "finance.draft_voucher.create",
        }:
            raise LocalExcelRejected("Excel sandbox action is not configured")
        with self._lock:
            if not self.path.exists():
                initialize_excel_sandbox(self.path)
            tables = _read_all_tables(self.path)
            digest = _payload_digest(action, payload)
            prior = _journal_lookup(
                tables["SystemJournal"],
                idempotency_key=idempotency_key,
                action=action,
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                payload_digest=digest,
            )
            if prior is not None:
                return prior
            if action == "customer.create":
                record_id, state, revision = self._customer_create(tables, payload)
            elif action == "customer.update":
                record_id, state, revision = self._customer_update(tables, payload)
            elif action == "procurement.request.create":
                record_id, state, revision = self._procurement_create(tables, payload)
            else:
                record_id, state, revision = self._draft_voucher_create(tables, payload)
            tables["SystemJournal"].append(
                [
                    idempotency_key,
                    action,
                    context.tenant_id,
                    context.actor_id,
                    digest,
                    record_id,
                    state,
                    revision,
                    _utcnow(),
                ]
            )
            _atomic_write_workbook(self.path, tables)
            return ActionReceipt(
                action=action,
                source_record_id=record_id,
                source_state=state,
                source_tenant_id=context.tenant_id,
                source_actor_id=context.actor_id,
                idempotency_key=idempotency_key,
                revision=revision,
            )

    @staticmethod
    def _customer_create(
        tables: dict[str, list[list[str]]],
        payload: Mapping[str, JSONValue],
    ) -> tuple[str, str, str]:
        record_id = _next_id(tables["Customers"], prefix="CUS", id_col=0)
        now = _utcnow()
        row = [
            record_id,
            _safe_text(payload.get("name")),
            _safe_text(payload.get("customer_type")),
            _safe_text(payload.get("phone")),
            _safe_text(payload.get("email")),
            _safe_text(payload.get("address")),
            "ACTIVE",
            "1",
            now,
            now,
        ]
        tables["Customers"].append(row)
        return record_id, "CREATED", "1"

    @staticmethod
    def _customer_update(
        tables: dict[str, list[list[str]]],
        payload: Mapping[str, JSONValue],
    ) -> tuple[str, str, str]:
        customer_id = _safe_text(payload.get("customer_id"))
        changes = payload.get("changes")
        if not isinstance(changes, Mapping) or not changes:
            raise LocalExcelRejected("customer update requires changes")
        index = _find_record(tables["Customers"], 0, customer_id)
        if index is None:
            raise LocalExcelRejected("customer unavailable")
        row = tables["Customers"][index]
        mapping = {
            "name": 1,
            "phone": 3,
            "email": 4,
            "address": 5,
            "status": 6,
        }
        for field, value in changes.items():
            if field not in mapping:
                raise LocalExcelRejected("customer field is not writable")
            row[mapping[field]] = _safe_text(value)
        revision = str(int(row[7] or "0") + 1)
        row[7] = revision
        row[9] = _utcnow()
        return customer_id, row[6] if row[6] in {"ACTIVE", "INACTIVE"} else "UPDATED", revision

    @staticmethod
    def _procurement_create(
        tables: dict[str, list[list[str]]],
        payload: Mapping[str, JSONValue],
    ) -> tuple[str, str, str]:
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise LocalExcelRejected("procurement request requires items")
        total = Decimal("0.00")
        normalized_items: list[tuple[str, Decimal, Decimal, Decimal]] = []
        for item in items:
            if not isinstance(item, Mapping):
                raise LocalExcelRejected("procurement item is invalid")
            quantity = _decimal(item.get("quantity"), field="quantity", positive=True)
            unit = _decimal(item.get("unit_estimated_cost"), field="unit_estimated_cost")
            line_total = (quantity * unit).quantize(Decimal("0.01"))
            total += line_total
            normalized_items.append((_safe_text(item.get("description")), quantity, unit, line_total))
        declared = _decimal(payload.get("estimated_total"), field="estimated_total")
        if total.quantize(Decimal("0.01")) != declared:
            raise LocalExcelRejected("procurement total mismatch")
        record_id = _next_id(tables["ProcurementRequests"], prefix="PR", id_col=0)
        now = _utcnow()
        tables["ProcurementRequests"].append(
            [
                record_id,
                _safe_text(payload.get("requester")),
                _safe_text(payload.get("department")),
                _safe_text(payload.get("currency")),
                _safe_text(payload.get("justification")),
                format(total, ".2f"),
                "DRAFT",
                "1",
                now,
            ]
        )
        for line_no, (description, quantity, unit, line_total) in enumerate(normalized_items, start=1):
            tables["ProcurementItems"].append(
                [
                    record_id,
                    str(line_no),
                    description,
                    format(quantity, ".2f"),
                    format(unit, ".2f"),
                    format(line_total, ".2f"),
                ]
            )
        return record_id, "DRAFT", "1"

    @staticmethod
    def _draft_voucher_create(
        tables: dict[str, list[list[str]]],
        payload: Mapping[str, JSONValue],
    ) -> tuple[str, str, str]:
        if _safe_text(payload.get("status")) != "DRAFT":
            raise LocalExcelRejected("voucher must remain DRAFT")
        entries = payload.get("entries")
        if not isinstance(entries, list) or len(entries) < 2:
            raise LocalExcelRejected("draft voucher requires at least two entries")
        total_debit = Decimal("0.00")
        total_credit = Decimal("0.00")
        normalized_entries: list[tuple[str, Decimal, Decimal, str]] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise LocalExcelRejected("voucher entry is invalid")
            debit = _decimal(entry.get("debit"), field="debit")
            credit = _decimal(entry.get("credit"), field="credit")
            if debit > 0 and credit > 0:
                raise LocalExcelRejected("voucher line cannot be both debit and credit")
            if debit == 0 and credit == 0:
                raise LocalExcelRejected("voucher line must contain debit or credit")
            total_debit += debit
            total_credit += credit
            normalized_entries.append(
                (
                    _safe_text(entry.get("account_code")),
                    debit,
                    credit,
                    _safe_text(entry.get("memo")),
                )
            )
        declared_debit = _decimal(payload.get("total_debit"), field="total_debit")
        declared_credit = _decimal(payload.get("total_credit"), field="total_credit")
        if total_debit != total_credit or total_debit == 0:
            raise LocalExcelRejected("voucher is not balanced")
        if total_debit != declared_debit or total_credit != declared_credit:
            raise LocalExcelRejected("voucher totals do not match lines")
        record_id = _next_id(tables["DraftVouchers"], prefix="DV", id_col=0)
        now = _utcnow()
        tables["DraftVouchers"].append(
            [
                record_id,
                _safe_text(payload.get("voucher_date")),
                _safe_text(payload.get("currency")),
                _safe_text(payload.get("description")),
                format(total_debit, ".2f"),
                format(total_credit, ".2f"),
                "DRAFT",
                "1",
                now,
            ]
        )
        for line_no, (account_code, debit, credit, memo) in enumerate(normalized_entries, start=1):
            tables["VoucherEntries"].append(
                [
                    record_id,
                    str(line_no),
                    account_code,
                    format(debit, ".2f"),
                    format(credit, ".2f"),
                    memo,
                ]
            )
        return record_id, "DRAFT", "1"


def initialize_excel_sandbox(path: str | Path) -> None:
    target = Path(path)
    if not target.is_absolute() or target.suffix.casefold() != ".xlsx":
        raise ValueError("sandbox path must be an absolute .xlsx path")
    tables = {sheet: [list(headers)] for sheet, headers in _HEADERS.items()}
    _atomic_write_workbook(target, tables)


def _read_all_tables(path: Path) -> dict[str, list[list[str]]]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise LocalExcelUnavailable("Excel workbook unavailable") from exc
    tables: dict[str, list[list[str]]] = {}
    for sheet, headers in _HEADERS.items():
        values = parse_xlsx_table(
            payload,
            sheet_name=sheet,
            max_rows=20_000,
            max_columns=max(len(headers), 16),
            max_uncompressed_bytes=max(len(payload) * 12, 1_000_000),
        )
        if not values:
            raise LocalExcelRejected(f"{sheet} sheet is empty")
        actual = tuple(_safe_text(v) for v in values[0])
        if actual != headers:
            raise LocalExcelRejected(f"{sheet} headers do not match the sandbox contract")
        rows: list[list[str]] = [list(headers)]
        for raw in values[1:]:
            row = [_safe_text(v) for v in raw]
            row.extend([""] * (len(headers) - len(row)))
            if len(row) != len(headers):
                raise LocalExcelRejected(f"{sheet} row shape is invalid")
            rows.append(row)
        tables[sheet] = rows
    return tables


def _journal_lookup(
    rows: list[list[str]],
    *,
    idempotency_key: str,
    action: str,
    tenant_id: str,
    actor_id: str,
    payload_digest: str,
) -> ActionReceipt | None:
    for row in rows[1:]:
        if row[0] != idempotency_key:
            continue
        if row[1] != action or row[2] != tenant_id or row[3] != actor_id or row[4] != payload_digest:
            raise LocalExcelConflict("Excel source idempotency key conflicts with an earlier mutation")
        return ActionReceipt(
            action=action,
            source_record_id=row[5],
            source_state=row[6],
            source_tenant_id=tenant_id,
            source_actor_id=actor_id,
            idempotency_key=idempotency_key,
            revision=row[7] or None,
        )
    return None


def _find_record(rows: list[list[str]], id_col: int, record_id: str) -> int | None:
    for index, row in enumerate(rows[1:], start=1):
        if row[id_col] == record_id:
            return index
    return None


def _next_id(rows: list[list[str]], *, prefix: str, id_col: int) -> str:
    maximum = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for row in rows[1:]:
        match = pattern.fullmatch(row[id_col])
        if match:
            maximum = max(maximum, int(match.group(1)))
    return f"{prefix}-{maximum + 1:06d}"


def _atomic_write_workbook(path: Path, tables: Mapping[str, list[list[str]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _build_xlsx(tables)
    fd, tmp_name = tempfile.mkstemp(prefix=".osai-excel-", suffix=".xlsx", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise LocalExcelUnavailable(
            "Excel workbook could not be replaced; close it in Excel and retry"
        ) from exc


def _build_xlsx(tables: Mapping[str, list[list[str]]]) -> bytes:
    missing = set(_HEADERS) - set(tables)
    if missing:
        raise LocalExcelRejected(f"missing Excel sandbox sheet(s): {sorted(missing)}")
    ordered = list(_HEADERS)
    workbook_sheets = []
    workbook_rels = []
    content_types = [
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
    ]
    sheet_xml: dict[str, str] = {}
    for index, sheet_name in enumerate(ordered, start=1):
        rel_id = f"rId{index}"
        workbook_sheets.append(
            f'<sheet name="{xml_escape(sheet_name)}" sheetId="{index}" r:id="{rel_id}"/>'
        )
        workbook_rels.append(
            '<Relationship '
            f'Id="{rel_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
        sheet_xml[f"xl/worksheets/sheet{index}.xml"] = _worksheet_xml(tables[sheet_name])

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(workbook_sheets)}</sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(workbook_rels)}</Relationships>'
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f'{"".join(content_types)}</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )

    import io

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        for name, xml in sheet_xml.items():
            archive.writestr(name, xml)
    return output.getvalue()


def _worksheet_xml(rows: list[list[str]]) -> str:
    xml_rows: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells: list[str] = []
        for column_index, value in enumerate(row, start=1):
            ref = f"{_excel_col(column_index)}{row_index}"
            text = xml_escape(_safe_text(value))
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    )


def _excel_col(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result
