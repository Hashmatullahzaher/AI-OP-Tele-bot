"""Strict local/cloud runtime configuration contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .contracts import ContractError


@dataclass(frozen=True)
class RuntimeConfig:
    profile: str
    bind_host: str
    port: int
    database_path: str
    secret_backend: str
    public_base_url: str | None
    outbound_hosts: tuple[str, ...]

    _FIELDS = frozenset(
        {
            "profile",
            "bind_host",
            "port",
            "database_path",
            "secret_backend",
            "public_base_url",
            "outbound_hosts",
        }
    )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> RuntimeConfig:
        unknown = set(raw) - cls._FIELDS
        if unknown:
            raise ContractError(f"unknown runtime config field(s): {sorted(unknown)}")
        profile = str(raw.get("profile", "local"))
        if profile not in {"local", "cloud"}:
            raise ContractError("profile must be local or cloud")
        host = str(raw.get("bind_host", "127.0.0.1" if profile == "local" else "0.0.0.0"))
        if profile == "local" and host not in {"127.0.0.1", "::1", "localhost"}:
            raise ContractError("local profile must bind only to loopback")
        port = int(raw.get("port", 8765))
        if port < 1 or port > 65535:
            raise ContractError("port out of range")
        db = str(raw.get("database_path", "./var/osai.sqlite3"))
        if not db.strip():
            raise ContractError("database_path is required")
        # Normalize only enough to reject obvious empty/directory mistakes; creation is deployment work.
        if Path(db).name in {"", ".", ".."}:
            raise ContractError("database_path must name a file")
        secret_backend = str(raw.get("secret_backend", "os_keyring" if profile == "local" else "managed"))
        if secret_backend not in {"os_keyring", "managed", "test"}:
            raise ContractError("unsupported secret_backend")
        public_url_raw = raw.get("public_base_url")
        public_url = None if public_url_raw in {None, ""} else str(public_url_raw)
        if profile == "cloud":
            if not public_url:
                raise ContractError("cloud profile requires public_base_url")
            parsed = urlparse(public_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ContractError("cloud public_base_url must be HTTPS")
        outbound = raw.get("outbound_hosts", [])
        if not isinstance(outbound, list) or not all(isinstance(v, str) and v.strip() for v in outbound):
            raise ContractError("outbound_hosts must be a list of host names")
        clean_hosts = tuple(sorted({v.lower().strip().rstrip(".") for v in outbound}))
        return cls(
            profile=profile,
            bind_host=host,
            port=port,
            database_path=db,
            secret_backend=secret_backend,
            public_base_url=public_url,
            outbound_hosts=clean_hosts,
        )
