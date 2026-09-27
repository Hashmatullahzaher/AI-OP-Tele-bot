"""OS AI Assistant — installable Windows desktop app.

Double-clicking the app starts a private server on 127.0.0.1 (random free
port) and opens the chat in the default browser. Launching it again while it
runs just reopens the browser. The Quit button in the page stops it.

Layout (per Windows user):
- ``%LOCALAPPDATA%\\OS AI Assistant``: settings, encrypted key, audit database.
- ``Documents\\OS AI Assistant Data``: the Excel/CSV files the user asks about.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import secrets
import socket
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from ..agent import SourceGroundedAgent
from ..bot.i18n import message
from ..bot.service import BotService, bootstrap_store
from ..connectors.local_files import LocalFileConnector, local_table_manifest
from ..contracts import CapabilityRegistry, ToolExecutor
from ..llm import PRESETS, build_planner, settings_from_env
from ..llm.config import LLMConfigError
from ..setup_config import MemorySecretStore, SecretStore, SecretUnavailable
from ..storage import TenantSecurityStore
from .discovery import DiscoveryResult, build_catalog, discover
from .pages import CHAT_PAGE, SETUP_PAGE, render

APP_NAME = "OS AI Assistant"
PRODUCT_ID = "os-ai-assistant"
KEY_SECRET = "llm.api_key"
NO_KEY_PRESETS = {"ollama", "lmstudio"}
MAX_BODY_BYTES = 16_384

log = logging.getLogger("osai.desktop")


# ---------------------------------------------------------------- locations


def app_root(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    if source.get("OSAI_DESKTOP_HOME"):
        return Path(source["OSAI_DESKTOP_HOME"])
    base = source.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def data_folder(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    if source.get("OSAI_DESKTOP_DATA"):
        return Path(source["OSAI_DESKTOP_DATA"])
    return Path.home() / "Documents" / f"{APP_NAME} Data"


class DevFileSecretStore:
    """Non-Windows development fallback: owner-only file, NOT encrypted."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, str]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}

    def _write(self, values: Mapping[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(dict(values)), encoding="utf-8")
        os.chmod(self.path, 0o600)

    def set(self, name: str, value: str) -> None:
        values = self._read()
        values[name] = value
        self._write(values)

    def get(self, name: str) -> str:
        try:
            return self._read()[name]
        except KeyError as exc:
            raise SecretUnavailable("secret unavailable") from exc

    def delete(self, name: str) -> None:
        values = self._read()
        values.pop(name, None)
        self._write(values)

    def configured(self, name: str) -> bool:
        return name in self._read()


def default_secret_store(root: Path) -> SecretStore:
    if os.name == "nt":
        from ..setup_config import WindowsDpapiSecretStore

        return WindowsDpapiSecretStore(root / "config" / "secrets.json", machine_scope=False)
    return DevFileSecretStore(root / "config" / "secrets.dev.json")


# ---------------------------------------------------------------- app state


class DesktopApp:
    """Settings, data discovery and the answering service (single thread)."""

    def __init__(self, *, root: Path, data_dir: Path, secret_store: SecretStore | None = None) -> None:
        self.root = root
        self.data_dir = data_dir
        self.settings_path = root / "config" / "settings.json"
        self.secrets = secret_store if secret_store is not None else default_secret_store(root)
        self._store: TenantSecurityStore | None = None
        self._service: BotService | None = None
        self._service_key: tuple[Any, ...] | None = None
        self._discovery: DiscoveryResult | None = None

    # settings
    def load_settings(self) -> dict[str, str]:
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {k: v for k, v in raw.items() if k in {"preset", "model", "base_url"} and isinstance(v, str)}

    def key_saved(self) -> bool:
        try:
            return self.secrets.configured(KEY_SECRET)
        except SecretUnavailable:
            return False

    def _llm_env(self, settings: Mapping[str, str], api_key: str | None) -> dict[str, str]:
        preset = settings.get("preset", "openrouter")
        env = {"OSAI_LLM_PRESET": preset, "OSAI_LLM_MODEL": settings.get("model", "")}
        if preset == "custom":
            env["OSAI_LLM_BASE_URL"] = settings.get("base_url", "")
        if preset in NO_KEY_PRESETS or (preset == "custom" and not api_key):
            env["OSAI_LLM_AUTH"] = "none"
        else:
            env["OSAI_LLM_API_KEY"] = api_key or ""
        return env

    def configured(self) -> bool:
        settings = self.load_settings()
        if not settings:
            return False
        try:
            self._llm_settings(settings)
        except (LLMConfigError, SecretUnavailable):
            return False
        return True

    def _llm_settings(self, settings: Mapping[str, str]) -> Any:
        preset = settings.get("preset", "openrouter")
        key = None if preset in NO_KEY_PRESETS else (self.secrets.get(KEY_SECRET) if self.key_saved() else None)
        return settings_from_env(self._llm_env(settings, key))

    def save_settings(self, payload: Mapping[str, Any]) -> None:
        preset = str(payload.get("preset", "")).strip()
        if preset != "custom" and preset not in PRESETS:
            raise ValueError("Choose an AI service from the list.")
        settings = {
            "preset": preset,
            "model": str(payload.get("model", "")).strip(),
            "base_url": str(payload.get("base_url", "")).strip(),
        }
        new_key = str(payload.get("api_key", "")).strip()
        key = new_key or (self.secrets.get(KEY_SECRET) if self.key_saved() else None)
        if preset not in NO_KEY_PRESETS and preset != "custom" and not key:
            raise ValueError("Paste the API key for this service.")
        if not settings["model"] and not (PRESETS.get(preset) and PRESETS[preset].default_model):
            raise ValueError("Enter the model name.")
        try:
            settings_from_env(self._llm_env(settings, key))  # validate before saving anything
        except LLMConfigError as exc:
            raise ValueError(str(exc)) from exc
        if new_key:
            self.secrets.set(KEY_SECRET, new_key)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        self._service = None  # rebuild with the new AI connection

    # data
    def refresh_data(self) -> DiscoveryResult:
        self._discovery = discover(self.data_dir)
        return self._discovery

    def status(self) -> dict[str, Any]:
        result = self.refresh_data()
        return {
            "configured": self.configured(),
            "data_folder": str(self.data_dir),
            "tables": [{"file": t.file_name, "sheet": t.sheet_name, "columns": list(t.columns)} for t in result.tables],
            "skipped": list(result.skipped),
        }

    # answering
    def _store_handle(self) -> TenantSecurityStore:
        if self._store is None:
            (self.root / "data").mkdir(parents=True, exist_ok=True)
            self._store = TenantSecurityStore(self.root / "data" / "osai.sqlite3")
        return self._store

    def _current_service(self) -> tuple[BotService | None, str | None]:
        result = self.refresh_data()
        settings = self.load_settings()
        key = (result.signature, tuple(sorted(settings.items())), self.key_saved())
        if self._service is not None and self._service_key == key:
            return self._service, None
        catalog = build_catalog(self.data_dir, result)
        if catalog is None:
            return None, "no_data"
        try:
            llm = self._llm_settings(settings)
        except (LLMConfigError, SecretUnavailable):
            return None, "not_configured"
        store = self._store_handle()
        bootstrap_store(store, catalog)
        registry = CapabilityRegistry()
        registry.register(
            local_table_manifest(),
            LocalFileConnector(resources={r.alias: r for r in catalog.local_resources}),
        )
        agent = SourceGroundedAgent(
            provider=build_planner(llm, catalog.tables),
            registry=registry,
            executor=ToolExecutor(registry),
            policy_check=store.authorize,
            audit_sink=store,
        )
        self._service = BotService(agent=agent, catalog=catalog)
        self._service_key = key
        return self._service, None

    def answer(self, text: str) -> dict[str, str]:
        from ..bot.i18n import detect_language

        service, problem = self._current_service()
        lang = detect_language(text)
        if problem == "no_data":
            return {"reply": _NO_DATA.get(lang, _NO_DATA["en"])}
        if problem == "not_configured" or service is None:
            return {"reply": _NOT_CONFIGURED.get(lang, _NOT_CONFIGURED["en"])}
        user = service.catalog.web_user()
        assert user is not None
        reply = service.answer_as(user, text)
        return {"reply": reply, "detail": service.last_error or ""}

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None


_NO_DATA = {
    "en": "There is no data yet. Click “Data folder”, copy your Excel or CSV files into it, then ask again.",
    "fa": "هنوز داده‌ای نیست. روی «Data folder» کلیک کنید، فایل‌های Excel یا CSV خود را در آن کاپی کنید و دوباره بپرسید.",
    "ps": "لا معلومات نشته. په «Data folder» کلیک وکړئ، خپل Excel یا CSV فایلونه پکې کاپي کړئ او بیا وپوښتئ.",
}
_NOT_CONFIGURED = {
    "en": "The AI service is not set up. Open Settings and save your AI service and key.",
    "fa": "سرویس هوش مصنوعی تنظیم نشده است. تنظیمات (Settings) را باز کرده و سرویس و کلید را ذخیره کنید.",
    "ps": "د مصنوعي ځیرکتیا خدمت نه دی تنظیم شوی. Settings پرانیزئ او خدمت او کیلي خوندي کړئ.",
}


# ---------------------------------------------------------------- HTTP server


def _script_json(value: Any) -> str:
    """JSON safe to embed inside a <script> element."""

    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


class DesktopServer(HTTPServer):
    # Single-threaded on purpose: one local user and one SQLite connection.
    app: DesktopApp
    token: str
    open_folder: Callable[[Path], None]


class _Handler(BaseHTTPRequestHandler):
    server: DesktopServer

    def _hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; "
            "form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Mapping[str, Any]) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(200, {"status": "alive", "product": PRODUCT_ID})
            return
        if self.headers.get("Host", "") not in self._hosts():
            self._json(403, {"error": "forbidden"})
            return
        app = self.server.app
        if self.path in {"/", "/index.html"} and app.configured():
            page = render(CHAT_PAGE, TOKEN=self.server.token)
        elif self.path in {"/", "/index.html", "/settings"}:
            settings = app.load_settings()
            current = {
                "preset": settings.get("preset", "openrouter"),
                "model": settings.get("model", ""),
                "base_url": settings.get("base_url", ""),
                "key_saved": app.key_saved(),
                "configured": app.configured(),
            }
            page = render(
                SETUP_PAGE,
                TOKEN=self.server.token,
                CURRENT=_script_json(current),
                DATA_FOLDER=html.escape(str(app.data_dir)),
            )
        else:
            self._json(404, {"error": "not found"})
            return
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        origin = self.headers.get("Origin")
        if (
            self.headers.get("Host", "") not in self._hosts()
            or (origin is not None and origin not in {f"http://{h}" for h in self._hosts()})
            or not secrets.compare_digest(self.headers.get("X-OSAI-Token", ""), self.server.token)
        ):
            self._json(403, {"error": "forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._json(413, {"error": "request too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
            if not isinstance(payload, dict):
                raise TypeError
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            self._json(400, {"error": "invalid request"})
            return
        app = self.server.app
        try:
            if self.path == "/api/ask":
                text = payload.get("text")
                if not isinstance(text, str) or not text.strip():
                    self._json(400, {"error": "empty question"})
                    return
                self._json(200, app.answer(text[:4_000]))
            elif self.path == "/api/status":
                self._json(200, app.status())
            elif self.path == "/api/settings":
                try:
                    app.save_settings(payload)
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
                    return
                self._json(200, {"ok": True})
            elif self.path == "/api/open-data-folder":
                app.data_dir.mkdir(parents=True, exist_ok=True)
                self.server.open_folder(app.data_dir)
                self._json(200, {"ok": True})
            elif self.path == "/api/quit":
                self._json(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._json(404, {"error": "not found"})
        except Exception:
            log.exception("request failed: %s", self.path)
            self._json(500, {"error": message("en", "error")})

    def log_message(self, format: str, *args: object) -> None:  # questions are never logged
        return


def _open_folder(path: Path) -> None:
    startfile = getattr(os, "startfile", None)
    if startfile is not None:
        startfile(str(path))
    else:  # pragma: no cover - development on macOS/Linux
        webbrowser.open(path.as_uri())


def build_server(app: DesktopApp, *, port: int = 0) -> DesktopServer:
    server = DesktopServer(("127.0.0.1", port), _Handler)
    server.app = app
    server.token = secrets.token_urlsafe(32)
    server.open_folder = _open_folder
    return server


# ---------------------------------------------------------------- launcher


def _port_file(root: Path) -> Path:
    return root / "config" / "port.txt"


def running_port(root: Path) -> int | None:
    try:
        port = int(_port_file(root).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return port if payload.get("product") == PRODUCT_ID else None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def serve(*, open_browser: bool, env: Mapping[str, str] | None = None) -> int:
    root = app_root(env)
    existing = running_port(root)
    if existing is not None:
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{existing}/")
        return 0
    root.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(root / "assistant.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = DesktopApp(root=root, data_dir=data_folder(env))
    app.data_dir.mkdir(parents=True, exist_ok=True)
    server = build_server(app, port=_free_port())
    port = server.server_address[1]
    _port_file(root).parent.mkdir(parents=True, exist_ok=True)
    _port_file(root).write_text(str(port), encoding="utf-8")
    url = f"http://127.0.0.1:{port}/"
    log.info("started on %s", url)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()
        try:
            if _port_file(root).read_text(encoding="utf-8").strip() == str(port):
                _port_file(root).unlink()
        except OSError:
            pass
        log.info("stopped")
    return 0


def check(env: Mapping[str, str] | None = None) -> int:
    root = app_root(env)
    app = DesktopApp(root=root, data_dir=data_folder(env), secret_store=MemorySecretStore())
    result = app.refresh_data()
    print(f"{APP_NAME}: app folder={root} data folder={app.data_dir} tables={len(result.tables)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="OS-AI-Assistant")
    parser.add_argument("command", nargs="?", default="run", choices=("run", "serve", "check"))
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "check":
        return check()
    return serve(open_browser=not args.no_browser and args.command == "run")
