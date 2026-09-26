"""Tenant-scoped, source-linked report artifacts for OS AI Core F6.

Reports are deterministic artifacts derived from already-authorized source rows.
This module does not query source systems or ask an LLM to calculate money.
Every artifact carries source revisions, a content digest and an expiry. Download
re-checks the active tenant actor and ownership before bytes are disclosed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from .contracts import ExecutionContext, JSONValue, PolicyDenied
from .storage import TenantSecurityStore


class ReportError(RuntimeError):
    """Base class for report failures."""


class ReportAccessDenied(ReportError):
    """The current actor cannot access the report."""


class ReportExpired(ReportAccessDenied):
    """The report link is expired."""


class ReportIntegrityError(ReportError):
    """Persisted artifact bytes no longer match their digest."""


class ReportSchemaError(ReportError):
    """The requested report cannot be generated without guessing semantics."""


@dataclass(frozen=True)
class ReportSource:
    source_id: str
    source_type: str
    revision: str
    locator: dict[str, JSONValue]

    def __post_init__(self) -> None:
        if not self.source_id or not self.source_type or not self.revision:
            raise ReportSchemaError("report source provenance is incomplete")


@dataclass(frozen=True)
class ReportSpec:
    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    sources: tuple[ReportSource, ...]
    period_start: str
    period_end: str
    currency_column: str | None = None
    amount_column: str | None = None

    def __post_init__(self) -> None:
        title = self.title.strip()
        if not title or len(title) > 120:
            raise ReportSchemaError("report title is invalid")
        if not self.columns or len(self.columns) > 100:
            raise ReportSchemaError("report columns are invalid")
        folded = [c.strip().casefold() for c in self.columns]
        if any(not c for c in folded) or len(folded) != len(set(folded)):
            raise ReportSchemaError("report columns are blank or duplicated")
        if not self.sources:
            raise ReportSchemaError("report requires source provenance")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.period_start) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", self.period_end
        ):
            raise ReportSchemaError("report period must use YYYY-MM-DD")
        if self.period_end < self.period_start:
            raise ReportSchemaError("report period is reversed")
        if len(self.rows) > 20_000:
            raise ReportSchemaError("report row count exceeds limit")
        width = len(self.columns)
        if any(len(row) != width for row in self.rows):
            raise ReportSchemaError("report row width does not match columns")
        for optional in (self.currency_column, self.amount_column):
            if optional is not None and optional not in self.columns:
                raise ReportSchemaError("report financial column is absent")

    def financial_summary(self) -> tuple[str | None, str | None]:
        if self.amount_column is None:
            return None, None
        amount_idx = self.columns.index(self.amount_column)
        currency_idx = self.columns.index(self.currency_column) if self.currency_column is not None else None
        total = Decimal("0")
        currencies: set[str] = set()
        for row in self.rows:
            raw = row[amount_idx].strip()
            if raw:
                try:
                    total += Decimal(raw)
                except InvalidOperation as exc:
                    raise ReportSchemaError("amount column contains non-numeric data") from exc
            if currency_idx is not None:
                currency = row[currency_idx].strip()
                if currency:
                    currencies.add(currency)
        if len(currencies) > 1:
            raise ReportSchemaError("report spans multiple currencies")
        single_currency: str | None = next(iter(currencies)) if currencies else None
        return _decimal_text(total), single_currency


@dataclass(frozen=True)
class ReportArtifact:
    report_id: str
    format: str
    filename: str
    mime_type: str
    sha256: str
    size_bytes: int
    expires_at: int
    row_count: int
    total_amount: str | None
    currency: str | None
    source_revisions: tuple[str, ...]


class ReportArtifactStore:
    """Filesystem artifact store with SQLite metadata and tenant checks."""

    _FORMATS = frozenset({"csv", "xlsx", "pdf"})

    def __init__(
        self,
        *,
        root_dir: str | Path,
        metadata_db: str | Path,
        tenant_store: TenantSecurityStore,
    ) -> None:
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self._tenant = tenant_store
        self._db = sqlite3.connect(str(metadata_db))
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS report_artifacts (
                report_id TEXT NOT NULL,
                format TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                row_count INTEGER NOT NULL,
                total_amount TEXT,
                currency TEXT,
                source_manifest_json TEXT NOT NULL,
                PRIMARY KEY (report_id, format)
            )
            """
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> ReportArtifactStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def create(
        self,
        *,
        context: ExecutionContext,
        spec: ReportSpec,
        formats: tuple[str, ...] = ("csv", "xlsx", "pdf"),
        now_epoch: int,
        ttl_seconds: int = 3600,
    ) -> tuple[ReportArtifact, ...]:
        self._assert_tenant_actor(context)
        if now_epoch < 0:
            raise ValueError("now_epoch must be non-negative")
        if ttl_seconds < 60 or ttl_seconds > 86_400:
            raise ValueError("ttl_seconds must be between 60 and 86400")
        if not formats or len(formats) != len(set(formats)) or any(fmt not in self._FORMATS for fmt in formats):
            raise ValueError("unsupported or duplicate report format")
        total_amount, currency = spec.financial_summary()
        report_id = secrets.token_urlsafe(24)
        slug = _slug(spec.title)
        period = f"{spec.period_start}_to_{spec.period_end}"
        source_manifest = [
            {
                "source_id": item.source_id,
                "source_type": item.source_type,
                "revision": item.revision,
                "locator": item.locator,
            }
            for item in spec.sources
        ]
        source_manifest_json = json.dumps(source_manifest, sort_keys=True, separators=(",", ":"))
        expires_at = now_epoch + ttl_seconds
        artifacts: list[ReportArtifact] = []
        try:
            self._db.execute("BEGIN IMMEDIATE")
            for fmt in formats:
                filename = f"{slug}_{period}.{fmt}"
                payload, mime_type = _render(fmt, spec, total_amount=total_amount, currency=currency)
                digest = hashlib.sha256(payload).hexdigest()
                relative_path = f"{report_id}.{fmt}"
                _atomic_write(self.root / relative_path, payload)
                self._db.execute(
                    """
                    INSERT INTO report_artifacts(
                        report_id,format,tenant_id,actor_id,filename,mime_type,relative_path,
                        sha256,size_bytes,created_at,expires_at,row_count,total_amount,currency,source_manifest_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        report_id,
                        fmt,
                        context.tenant_id,
                        context.actor_id,
                        filename,
                        mime_type,
                        relative_path,
                        digest,
                        len(payload),
                        now_epoch,
                        expires_at,
                        len(spec.rows),
                        total_amount,
                        currency,
                        source_manifest_json,
                    ),
                )
                artifacts.append(
                    ReportArtifact(
                        report_id=report_id,
                        format=fmt,
                        filename=filename,
                        mime_type=mime_type,
                        sha256=digest,
                        size_bytes=len(payload),
                        expires_at=expires_at,
                        row_count=len(spec.rows),
                        total_amount=total_amount,
                        currency=currency,
                        source_revisions=tuple(item.revision for item in spec.sources),
                    )
                )
            self._db.commit()
        except Exception:
            self._db.rollback()
            for fmt in formats:
                try:
                    (self.root / f"{report_id}.{fmt}").unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        self._tenant.append_audit(
            context=context,
            event="report.created",
            result="OK",
            resource_scope=f"report:{report_id}",
            sensitive_payload={"formats": list(formats), "rows": len(spec.rows), "sources": len(spec.sources)},
        )
        return tuple(artifacts)

    def download(
        self,
        *,
        context: ExecutionContext,
        report_id: str,
        format: str,
        now_epoch: int,
    ) -> tuple[ReportArtifact, bytes]:
        self._assert_tenant_actor(context)
        if format not in self._FORMATS or not report_id:
            raise ReportAccessDenied("report unavailable")
        row = self._db.execute(
            "SELECT * FROM report_artifacts WHERE report_id=? AND format=?",
            (report_id, format),
        ).fetchone()
        if row is None:
            raise ReportAccessDenied("report unavailable")
        if str(row["tenant_id"]) != context.tenant_id or str(row["actor_id"]) != context.actor_id:
            raise ReportAccessDenied("report unavailable")
        if now_epoch < 0 or now_epoch >= int(row["expires_at"]):
            raise ReportExpired("report unavailable")
        path = self.root / str(row["relative_path"])
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ReportIntegrityError("report artifact unavailable") from exc
        digest = hashlib.sha256(payload).hexdigest()
        if not secrets.compare_digest(digest, str(row["sha256"])) or len(payload) != int(row["size_bytes"]):
            raise ReportIntegrityError("report artifact integrity check failed")
        source_manifest = json.loads(str(row["source_manifest_json"]))
        artifact = ReportArtifact(
            report_id=str(row["report_id"]),
            format=str(row["format"]),
            filename=str(row["filename"]),
            mime_type=str(row["mime_type"]),
            sha256=str(row["sha256"]),
            size_bytes=int(row["size_bytes"]),
            expires_at=int(row["expires_at"]),
            row_count=int(row["row_count"]),
            total_amount=None if row["total_amount"] is None else str(row["total_amount"]),
            currency=None if row["currency"] is None else str(row["currency"]),
            source_revisions=tuple(str(item["revision"]) for item in source_manifest),
        )
        self._tenant.append_audit(
            context=context,
            event="report.downloaded",
            result="OK",
            resource_scope=f"report:{report_id}",
            sensitive_payload={"format": format, "size_bytes": len(payload)},
        )
        return artifact, payload

    def _assert_tenant_actor(self, context: ExecutionContext) -> None:
        try:
            self._tenant.assert_actor(context, tenant_data=True)
        except PolicyDenied as exc:
            raise ReportAccessDenied("report unavailable") from exc


def _slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        normalized = "report"
    return normalized[:64]


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def _render(fmt: str, spec: ReportSpec, *, total_amount: str | None, currency: str | None) -> tuple[bytes, str]:
    if fmt == "csv":
        return _render_csv(spec), "text/csv; charset=utf-8"
    if fmt == "xlsx":
        return _render_xlsx(spec), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if fmt == "pdf":
        return _render_pdf(spec, total_amount=total_amount, currency=currency), "application/pdf"
    raise ValueError("unsupported report format")


def _render_csv(spec: ReportSpec) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(spec.columns)
    writer.writerows(spec.rows)
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _render_xlsx(spec: ReportSpec) -> bytes:
    output = io.BytesIO()
    rows = [spec.columns, *spec.rows]
    sheet_rows: list[str] = []
    for r_idx, row in enumerate(rows, start=1):
        cells = []
        for c_idx, value in enumerate(row, start=1):
            ref = f"{_excel_col(c_idx)}{r_idx}"
            text = xml_escape(value, {'"': '&quot;'})
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        sheet_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>'''
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return output.getvalue()


def _excel_col(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _render_pdf(spec: ReportSpec, *, total_amount: str | None, currency: str | None) -> bytes:
    lines = [
        spec.title,
        f"Period: {spec.period_start} to {spec.period_end}",
        f"Rows: {len(spec.rows)}",
        "Sources: " + ", ".join(f"{s.source_id}@{s.revision}" for s in spec.sources),
    ]
    if total_amount is not None:
        suffix = f" {currency}" if currency else ""
        lines.append(f"Total: {total_amount}{suffix}")
    lines.append(" | ".join(spec.columns))
    for row in spec.rows[:200]:
        lines.append(" | ".join(row))
    for line in lines:
        try:
            line.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise ReportSchemaError("PDF Unicode font is not configured for this report") from exc
    content_lines = ["BT", "/F1 9 Tf", "40 800 Td", "12 TL"]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content_lines.append(f"({escaped}) Tj")
        content_lines.append("T*")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{idx} 0 obj\n".encode("ascii"))
        out.write(obj)
        out.write(b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects)+1}\n".encode("ascii"))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.write(
        f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return out.getvalue()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".report-", dir=str(path.parent))
    try:
        os.chmod(tmp_name, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
