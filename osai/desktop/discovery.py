"""Discover Excel/CSV files in the user's data folder and describe them.

Every ``.csv`` becomes one table; every worksheet of an ``.xlsx`` becomes one
table. Only header names (never cell values) are passed to the AI planner.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..bot.catalog import BotCatalog, catalog_from_mapping
from ..connectors.google_drive import _safe_xml, normalize_cell, parse_xlsx_table

MAX_FILES = 50
MAX_FILE_BYTES = 20_000_000
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@dataclass(frozen=True)
class DiscoveredTable:
    file_name: str
    sheet_name: str | None
    columns: tuple[str, ...]


@dataclass(frozen=True)
class DiscoveryResult:
    tables: tuple[DiscoveredTable, ...]
    skipped: tuple[str, ...]  # "file: reason" lines shown to the user
    signature: tuple[tuple[str, int, int], ...]  # (name, size, mtime) to detect changes


def _slug(text: str, used: set[str], *, prefix: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    if not base or not base[0].isalpha():
        base = f"{prefix}-{base}".strip("-")
    base = base[:48] if len(base) >= 3 else f"{prefix}-{base}"
    alias, n = base, 2
    while alias in used:
        alias = f"{base}-{n}"
        n += 1
    used.add(alias)
    return alias


def xlsx_sheet_names(payload: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        workbook = _safe_xml(archive.read("xl/workbook.xml"))
    return [str(sheet.get("name")) for sheet in workbook.findall("m:sheets/m:sheet", _NS) if sheet.get("name")]


def _headers(values: Sequence[Sequence[object]]) -> tuple[str, ...]:
    if not values:
        raise ValueError("empty")
    headers = tuple(normalize_cell(v).strip() for v in values[0])
    while headers and not headers[-1]:
        headers = headers[:-1]
    if not headers or any(not h for h in headers):
        raise ValueError("first row must contain a name for every column")
    if len({h.casefold() for h in headers}) != len(headers):
        raise ValueError("duplicate column names in first row")
    return headers


def discover(folder: Path) -> DiscoveryResult:
    folder.mkdir(parents=True, exist_ok=True)
    tables: list[DiscoveredTable] = []
    skipped: list[str] = []
    signature: list[tuple[str, int, int]] = []
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".csv", ".xlsx"})
    for path in files[:MAX_FILES]:
        if path.name.startswith("~$"):
            continue  # Excel lock file of an open workbook
        stat = path.stat()
        signature.append((path.name, stat.st_size, int(stat.st_mtime)))
        if stat.st_size > MAX_FILE_BYTES:
            skipped.append(f"{path.name}: larger than 20 MB")
            continue
        try:
            payload = path.read_bytes()
            if path.suffix.lower() == ".csv":
                text = payload.decode("utf-8-sig")
                rows = [list(r) for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
                tables.append(DiscoveredTable(path.name, None, _headers(rows[:1])))
            else:
                for sheet in xlsx_sheet_names(payload):
                    try:
                        values = parse_xlsx_table(
                            payload,
                            sheet_name=sheet,
                            max_rows=20_001,
                            max_columns=100,
                            max_uncompressed_bytes=MAX_FILE_BYTES * 5,
                        )
                        tables.append(DiscoveredTable(path.name, sheet, _headers(values)))
                    except Exception as exc:  # one bad sheet must not hide the others
                        skipped.append(f"{path.name} / {sheet}: {_reason(exc)}")
        except UnicodeDecodeError:
            skipped.append(f"{path.name}: CSV must be saved as UTF-8")
        except Exception as exc:
            skipped.append(f"{path.name}: {_reason(exc)}")
    if len(files) > MAX_FILES:
        skipped.append(f"only the first {MAX_FILES} files are used")
    return DiscoveryResult(tuple(tables), tuple(skipped), tuple(signature))


def _reason(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    return text if len(text) <= 120 else text[:117] + "..."


def build_catalog(folder: Path, result: DiscoveryResult) -> BotCatalog | None:
    """Catalog for the single local owner; None when no usable table exists."""

    if not result.tables:
        return None
    used: set[str] = set()
    by_file: dict[str, list[DiscoveredTable]] = {}
    for table in result.tables:
        by_file.setdefault(table.file_name, []).append(table)
    resources: list[dict[str, Any]] = []
    for file_name, file_tables in by_file.items():
        alias = _slug(Path(file_name).stem, used, prefix="file")
        table_used: set[str] = set()
        resources.append(
            {
                "alias": alias,
                "kind": "csv" if file_name.lower().endswith(".csv") else "xlsx_file",
                "path": str((folder / file_name).resolve()),
                "tables": [
                    {
                        "alias": _slug(t.sheet_name or "rows", table_used, prefix="sheet"),
                        "sheet_name": t.sheet_name,
                        "description": f"File {file_name}" + (f", sheet {t.sheet_name}" if t.sheet_name else ""),
                        "columns": list(t.columns),
                    }
                    for t in file_tables
                ],
            }
        )
    return catalog_from_mapping(
        {
            "tenant_id": "local-desktop",
            "resources": resources,
            "roles": {"owner": {"resources": ["*"]}},
            "users": [{"telegram_user_id": 1, "actor_id": "owner", "role": "owner"}],
            "web_user": "owner",
        },
        base_dir=folder,
    )
