"""Operator-owned bot configuration: data sources, roles and Telegram users.

The file lives on the server (never in Git) because it contains private Drive
IDs and Telegram user IDs. See ``deploy/bot/catalog.example.json``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..connectors.google_drive import DriveResource, DriveTable
from ..llm.planner import TableDescription

_ALIAS_RE = re.compile(r"[a-z][a-z0-9_-]{1,63}")


class CatalogError(ValueError):
    """Invalid bot catalog (messages never contain Drive IDs)."""


@dataclass(frozen=True)
class BotUser:
    telegram_user_id: int
    actor_id: str
    role: str


@dataclass(frozen=True)
class BotCatalog:
    tenant_id: str
    resources: tuple[DriveResource, ...]
    tables: tuple[TableDescription, ...]
    roles: dict[str, tuple[str, ...]]
    users: tuple[BotUser, ...]

    def user(self, telegram_user_id: int) -> BotUser | None:
        for user in self.users:
            if user.telegram_user_id == telegram_user_id:
                return user
        return None

    def scopes_for(self, role: str) -> tuple[str, ...]:
        allowed = self.roles.get(role, ())
        aliases = [r.alias for r in self.resources] if "*" in allowed else list(allowed)
        return tuple(f"drive:{alias}" for alias in sorted(aliases))

    def tables_for(self, role: str) -> tuple[TableDescription, ...]:
        scopes = set(self.scopes_for(role))
        return tuple(t for t in self.tables if f"drive:{t.resource_alias}" in scopes)


def _str(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{where}.{key} must be a non-empty string")
    return value.strip()


def catalog_from_mapping(raw: Any) -> BotCatalog:
    if not isinstance(raw, dict):
        raise CatalogError("catalog must be a JSON object")
    tenant_id = _str(raw, "tenant_id", "catalog")
    resources: list[DriveResource] = []
    tables: list[TableDescription] = []
    for index, item in enumerate(raw.get("resources") or []):
        where = f"resources[{index}]"
        if not isinstance(item, dict):
            raise CatalogError(f"{where} must be an object")
        drive_tables: list[DriveTable] = []
        for t_index, table in enumerate(item.get("tables") or []):
            t_where = f"{where}.tables[{t_index}]"
            if not isinstance(table, dict):
                raise CatalogError(f"{t_where} must be an object")
            columns = table.get("columns")
            if not isinstance(columns, list) or not columns or not all(isinstance(c, str) and c for c in columns):
                raise CatalogError(f"{t_where}.columns must list the header names")
            drive_table = DriveTable(
                alias=_str(table, "alias", t_where),
                sheet_name=_str(table, "sheet_name", t_where),
                a1_range=table.get("range") or None,
            )
            drive_tables.append(drive_table)
            tables.append(
                TableDescription(
                    resource_alias=_str(item, "alias", where),
                    table_alias=drive_table.alias,
                    description=str(table.get("description", "")).strip()[:500],
                    columns=tuple(columns),
                )
            )
        if not drive_tables:
            raise CatalogError(f"{where}.tables must not be empty")
        try:
            resources.append(
                DriveResource(
                    alias=_str(item, "alias", where),
                    file_id=_str(item, "file_id", where),
                    kind=_str(item, "kind", where),
                    expected_parent_id=_str(item, "parent_id", where),
                    tables=tuple(drive_tables),
                )
            )
        except ValueError as exc:
            raise CatalogError(f"{where}: {exc}") from exc
    if not resources:
        raise CatalogError("catalog.resources must not be empty")
    aliases = {r.alias for r in resources}

    roles_raw = raw.get("roles")
    if not isinstance(roles_raw, dict) or not roles_raw:
        raise CatalogError("catalog.roles must map role names to resource lists")
    roles: dict[str, tuple[str, ...]] = {}
    for name, spec in roles_raw.items():
        allowed = spec.get("resources") if isinstance(spec, dict) else None
        if not isinstance(name, str) or not _ALIAS_RE.fullmatch(name) or not isinstance(allowed, list):
            raise CatalogError("each role needs a lowercase name and a resources list")
        unknown = {a for a in allowed if a != "*"} - aliases
        if unknown:
            raise CatalogError(f"role {name} references unknown resources")
        roles[name] = tuple(str(a) for a in allowed)

    users: list[BotUser] = []
    for index, item in enumerate(raw.get("users") or []):
        where = f"users[{index}]"
        if not isinstance(item, dict):
            raise CatalogError(f"{where} must be an object")
        telegram_id = item.get("telegram_user_id")
        if isinstance(telegram_id, bool) or not isinstance(telegram_id, int) or telegram_id <= 0:
            raise CatalogError(f"{where}.telegram_user_id must be a positive integer")
        role = _str(item, "role", where)
        if role not in roles:
            raise CatalogError(f"{where}.role is not defined in roles")
        users.append(BotUser(telegram_id, _str(item, "actor_id", where), role))
    if len({u.telegram_user_id for u in users}) != len(users):
        raise CatalogError("duplicate telegram_user_id in users")
    return BotCatalog(tenant_id, tuple(resources), tuple(tables), roles, tuple(users))


def load_catalog(path: str | Path) -> BotCatalog:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError("catalog file is missing or not valid JSON") from exc
    return catalog_from_mapping(raw)
