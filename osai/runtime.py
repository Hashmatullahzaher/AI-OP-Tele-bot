"""Minimal operational runtime/check surface for F8 and Windows UAT packaging.

Only health/readiness and a local operator status page are exposed here.
Business/chat/API routes remain separate capability/channel work and must not be
implied by this server.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .config import RuntimeConfig
from .deployment import ReadinessReport, evaluate_readiness
from .setup_config import SetupError, SetupValidationError
from .setup_wizard import (
    SetupWebController,
    setup_access_token_for_runtime,
    setup_manager_for_runtime,
)
from .storage import TenantSecurityStore

CORE_VERSION = "0.1.0"

_OPERATOR_DASHBOARD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OS AI Core</title>
<style>
:root{font-family:Segoe UI,Arial,sans-serif;color:#1f2937;background:#f4f7fb}
body{margin:0;padding:40px}.shell{max-width:760px;margin:auto}.card{background:#fff;border-radius:16px;padding:24px;
box-shadow:0 8px 28px rgba(15,23,42,.08);margin-bottom:16px}.row{display:flex;justify-content:space-between;
gap:16px;align-items:center}.pill{padding:6px 10px;border-radius:999px;background:#eef2ff;font-weight:600}
small{color:#64748b}code{background:#f1f5f9;padding:2px 6px;border-radius:6px}.ok{background:#dcfce7}.warn{background:#fef3c7}
</style>
</head>
<body>
<div class="shell">
<div class="card"><div class="row"><div><h1>OS AI Core</h1><small>Local Operator Dashboard</small></div>
<span id="health" class="pill">Checking...</span></div></div>
<div class="card"><h2>Runtime</h2><p>This page is served by the local Core runtime, not the demo application.</p>
<p>Local endpoint: <code id="endpoint"></code></p><p id="ready">Readiness: checking...</p></div>
<div class="card"><h2>Security boundary</h2><p>The Windows local profile binds to loopback by default. No public firewall
rule is created by the installer. Telegram, hosted AI, Drive, and customer APIs stay unavailable until separately configured.</p></div>
<div class="card"><h2>Configuration</h2><p>Open Setup from the launcher for your deployment. Installer mode uses an administrator-gated shortcut; Portable mode uses its local control window.</p></div>
</div>
<script>
document.getElementById('endpoint').textContent=location.origin;
async function refresh(){
 try{const h=await fetch('/healthz',{cache:'no-store'});const j=await h.json();
 const e=document.getElementById('health');e.textContent=j.status==='alive'?'Running':'Unavailable';
 e.className='pill '+(j.status==='alive'?'ok':'warn');}catch(_){document.getElementById('health').textContent='Unavailable';}
 try{const r=await fetch('/readyz',{cache:'no-store'});const j=await r.json();
 document.getElementById('ready').textContent='Readiness: '+(j.ready?'ready':'degraded');}catch(_){
 document.getElementById('ready').textContent='Readiness: unavailable';}}
refresh();setInterval(refresh,5000);
</script>
</body>
</html>
"""


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


class _RuntimeServer(ThreadingHTTPServer):
    setup_controller: SetupWebController | None


class _HealthHandler(BaseHTTPRequestHandler):
    server: _RuntimeServer
    server_version = "OSAIHealth/0.1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/index.html"}:
            self._html(200, _OPERATOR_DASHBOARD)
            return
        if self.path == "/healthz":
            self._json(200, {"status": "alive", "version": CORE_VERSION})
            return
        if self.path == "/readyz":
            report = readiness_from_env()
            self._json(200 if report.ready else 503, report.as_dict())
            return
        if self.path == "/setup":
            controller = self._setup_controller()
            if controller is None or not self._trusted_loopback_host():
                self._json(404, {"status": "not_found"})
                return
            self._html(200, controller.render())
            return
        if self.path == "/api/setup/status":
            controller = self._setup_controller()
            if controller is None or not self._trusted_loopback_host():
                self._json(404, {"status": "not_found"})
                return
            if not controller.verify_access(self.headers.get("X-OSAI-Setup-Access")):
                self._json(403, {"error": "administrator setup access required"})
                return
            self._json(200, controller.status())
            return
        if self.path == "/favicon.ico":
            self.send_response(204)
            self._security_headers()
            self.end_headers()
            return
        self._json(404, {"status": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        controller = self._setup_controller()
        if controller is None or not self.path.startswith("/api/setup"):
            self._json(404, {"status": "not_found"})
            return
        if not self._trusted_loopback_host() or not self._trusted_origin():
            self._json(403, {"error": "setup request rejected"})
            return
        if not controller.verify_access(self.headers.get("X-OSAI-Setup-Access")):
            self._json(403, {"error": "administrator setup access required"})
            return
        if not controller.verify_csrf(self.headers.get("X-OSAI-CSRF")):
            self._json(403, {"error": "setup request rejected"})
            return
        content_type = self.headers.get("Content-Type", "")
        if not content_type.casefold().startswith("application/json"):
            self._json(415, {"error": "application/json required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "invalid request length"})
            return
        if length < 0 or length > 32768:
            self._json(413, {"error": "setup request too large"})
            return
        try:
            raw = self.rfile.read(length)
            parsed = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
                raise SetupValidationError("setup request must be an object")
            if self.path == "/api/setup":
                result = controller.apply(parsed)
            elif self.path == "/api/setup/excel/init":
                if parsed:
                    raise SetupValidationError("Excel initialization request must be empty")
                result = controller.initialize_excel()
            else:
                self._json(404, {"status": "not_found"})
                return
        except (UnicodeDecodeError, json.JSONDecodeError, SetupValidationError) as exc:
            self._json(400, {"error": str(exc)})
            return
        except SetupError:
            self._json(500, {"error": "setup service unavailable"})
            return
        self._json(200, result)

    def _setup_controller(self) -> SetupWebController | None:
        return self.server.setup_controller

    def _trusted_loopback_host(self) -> bool:
        host = self.headers.get("Host", "").casefold().strip()
        port = int(self.server.server_address[1])
        return host in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    def _trusted_origin(self) -> bool:
        origin = self.headers.get("Origin", "").strip()
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        if parsed.scheme != "http" or parsed.username or parsed.password:
            return False
        port = int(self.server.server_address[1])
        try:
            origin_port = parsed.port
        except ValueError:
            return False
        host = (parsed.hostname or "").casefold().rstrip(".")
        return host in {"127.0.0.1", "localhost", "::1"} and origin_port == port

    def log_message(self, format: str, *args: object) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )

    def _json(self, status: int, payload: Mapping[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status: int, payload: str) -> None:
        body = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_server(config: RuntimeConfig) -> _RuntimeServer:
    server = _RuntimeServer((config.bind_host, config.port), _HealthHandler)
    controller: SetupWebController | None = None
    if config.profile == "local" and (config.secret_backend == "test" or os.name == "nt"):
        controller = SetupWebController(
            config=config,
            manager=setup_manager_for_runtime(config),
            access_token=setup_access_token_for_runtime(config),
        )
    server.setup_controller = controller
    return server


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
    server = build_server(config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
