"""Minimal operational runtime/check surface for F8.

Only health/readiness endpoints are exposed here. Business/chat/API routes remain
separate capability/channel work and must not be implied by this server.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import RuntimeConfig
from .deployment import ReadinessReport, evaluate_readiness
from .storage import TenantSecurityStore

CORE_VERSION = "0.1.0"


def _csv_env(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def config_from_env(env: Mapping[str, str] | None = None) -> RuntimeConfig:
    source = os.environ if env is None else env
    profile = source.get("OSAI_PROFILE", "local")
    raw: dict[str, object] = {
        "profile": profile,
        "bind_host": source.get(
            "OSAI_BIND_HOST",
            "127.0.0.1" if profile == "local" else "0.0.0.0",
        ),
        "port": int(source.get("OSAI_PORT", "8765")),
        "database_path": source.get("OSAI_DATABASE_PATH", "./var/osai.sqlite3"),
        "secret_backend": source.get(
            "OSAI_SECRET_BACKEND",
            "os_keyring" if profile == "local" else "managed",
        ),
        "outbound_hosts": [
            item.strip()
            for item in source.get("OSAI_OUTBOUND_HOSTS", "").split(",")
            if item.strip()
        ],
    }
    public = source.get("OSAI_PUBLIC_BASE_URL")
    if public:
        raw["public_base_url"] = public
    return RuntimeConfig.from_mapping(raw)


def readiness_from_env(env: Mapping[str, str] | None = None) -> ReadinessReport:
    source = os.environ if env is None else env
    def split(name: str) -> tuple[str, ...]:
        return tuple(item.strip() for item in source.get(name, "").split(",") if item.strip())
    return evaluate_readiness(
        network_mode=source.get("OSAI_NETWORK_MODE", "online"),
        required_external=split("OSAI_REQUIRED_EXTERNAL"),
        configured_external=split("OSAI_CONFIGURED_EXTERNAL"),
        degraded_external=split("OSAI_DEGRADED_EXTERNAL"),
    )


def startup_check(config: RuntimeConfig) -> None:
    db_path = Path(config.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with TenantSecurityStore(db_path):
        pass


class _HealthHandler(BaseHTTPRequestHandler):
    server_version = "OSAIHealth/0.1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(200, {"status": "alive", "version": CORE_VERSION})
            return
        if self.path == "/readyz":
            report = readiness_from_env()
            self._json(200 if report.ready else 503, report.as_dict())
            return
        self._json(404, {"status": "not_found"})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: int, payload: Mapping[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m osai.runtime")
    parser.add_argument("command", choices=("check", "serve"), nargs="?", default="check")
    args = parser.parse_args()
    config = config_from_env()
    startup_check(config)
    report = readiness_from_env()
    if args.command == "check":
        print(
            json.dumps(
                {
                    "core_version": CORE_VERSION,
                    "profile": config.profile,
                    "bind_host": config.bind_host,
                    "port": config.port,
                    "readiness": report.as_dict(),
                },
                separators=(",", ":"),
            )
        )
        return 0 if report.ready else 3
    server = ThreadingHTTPServer((config.bind_host, config.port), _HealthHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
