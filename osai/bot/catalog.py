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
from ..connectors.local_files import LOCAL_CAPABILITY, LocalResource, LocalTable
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
    local_resources: tuple[LocalResource, ...] = ()
    web_actor_id: str | None = None

    def user(self, telegram_user_id: int) -> BotUser | None:
        for user in self.users:
            if user.telegram_user_id == telegram_user_id:
                return user
        return None

    def web_user(self) -> BotUser | None:
        """The identity the localhost web chat acts as (``web_user`` or the first user)."""

        for user in self.users:
            if self.web_actor_id is None or user.actor_id == self.web_actor_id:
                return user
        return None

    def scopes_for(self, role: str) -> tuple[str, ...]:
        allowed = set(self.roles.get(role, ()))
        scopes = [f"drive:{r.alias}" for r in self.resources if "*" in allowed or r.alias in allowed]
        scopes += [f"local:{r.alias}" for r in self.local_resources if "*" in allowed or r.alias in allowed]
        return tuple(sorted(scopes))


def _str(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{where}.{key} must be a non-empty string")
    return value.strip()


def catalog_from_mapping(raw: Any, *, base_dir: Path | None = None) -> BotCatalog:
    base_dir = base_dir or Path.cwd()
    if not isinstance(raw, dict):
        raise CatalogError("catalog must be a JSON object")
    tenant_id = _str(raw, "tenant_id", "catalog")
    resources: list[DriveResource] = []
    tables: list[TableDescription] = []
    local_resources: list[LocalResource] = []
    for index, item in enumerate(raw.get("resources") or []):
        where = f"resources[{index}]"
        if not isinstance(item, dict):
            raise CatalogError(f"{where} must be an object")
        alias = _str(item, "alias", where)
        kind = _str(item, "kind", where)
        is_local = kind in {"csv", "xlsx_file"}
        drive_tables: list[DriveTable] = []
        local_tables: list[LocalTable] = []
        for t_index, table in enumerate(item.get("tables") or []):
            t_where = f"{where}.tables[{t_index}]"
            if not isinstance(table, dict):
                raise CatalogError(f"{t_where} must be an object")
            columns = table.get("columns")
            if not isinstance(columns, list) or not columns or not all(isinstance(c, str) and c for c in columns):
                raise CatalogError(f"{t_where}.columns must list the header names")
            try:
                if is_local:
                    local_table = LocalTable(alias=_str(table, "alias", t_where), sheet_name=table.get("sheet_name"))
                    local_tables.append(local_table)
                    table_alias = local_table.alias
                else:
                    drive_table = DriveTable(
                        alias=_str(table, "alias", t_where),
                        sheet_name=_str(table, "sheet_name", t_where),
                        a1_range=table.get("range") or None,
                    )
                    drive_tables.append(drive_table)
                    table_alias = drive_table.alias
            except ValueError as exc:
                raise CatalogError(f"{t_where}: {exc}") from exc
            tables.append(
                TableDescription(
                    resource_alias=alias,
                    table_alias=table_alias,
                    description=str(table.get("description", "")).strip()[:500],
                    columns=tuple(columns),
                    capability=LOCAL_CAPABILITY if is_local else "drive.table.read",
                )
            )
        if not drive_tables and not local_tables:
            raise CatalogError(f"{where}.tables must not be empty")
        try:
            if is_local:
                path = Path(_str(item, "path", where))
                if not path.is_absolute():
                    path = (base_dir / path).resolve()
                local_resources.append(LocalResource(alias=alias, path=path, kind=kind, tables=tuple(local_tables)))
            else:
                resources.append(
                    DriveResource(
                        alias=alias,
                        file_id=_str(item, "file_id", where),
                        kind=kind,
                        expected_parent_id=_str(item, "parent_id", where),
                        tables=tuple(drive_tables),
                    )
                )
        except ValueError as exc:
            raise CatalogError(f"{where}: {exc}") from exc
    if not resources and not local_resources:
        raise CatalogError("catalog.resources must not be empty")
    aliases = {r.alias for r in resources} | {r.alias for r in local_resources}
    if len(aliases) != len(resources) + len(local_resources):
        raise CatalogError("duplicate resource alias")

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
    web_actor = raw.get("web_user")
    if web_actor is not None and web_actor not in {u.actor_id for u in users}:
        raise CatalogError("web_user must be the actor_id of one of the users")
    return BotCatalog(
        tenant_id,
        tuple(resources),
        tuple(tables),
        roles,
        tuple(users),
        local_resources=tuple(local_resources),
        web_actor_id=web_actor,
    )


def load_catalog(path: str | Path) -> BotCatalog:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError("catalog file is missing or not valid JSON") from exc
    return catalog_from_mapping(raw, base_dir=Path(path).resolve().parent)
