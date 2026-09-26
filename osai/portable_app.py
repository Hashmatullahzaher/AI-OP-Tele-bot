"""One-click Windows portable host for OS AI Core.

This is a per-user local UAT host: no installer, no Windows service, and no
administrator elevation. It starts the same loopback runtime and provides a
small Tk control window with Dashboard, Setup, Data Folder, and Stop controls.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from collections.abc import MutableMapping
from pathlib import Path

from .runtime import build_server, config_from_env, readiness_from_env, startup_check
from .setup_wizard import setup_access_token_for_runtime

APP_NAME = "OS AI Core Portable"
RESERVED_PORTS = {8765, 8766}


def portable_data_root(env: MutableMapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    base = source.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "OS AI Core Portable"


def _state_path(env: MutableMapping[str, str] | None = None) -> Path:
    return portable_data_root(env) / "config" / "runtime-port.txt"


def _health_alive_at(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/healthz", timeout=0.75
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("status") == "alive"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _existing_port(env: MutableMapping[str, str] | None = None) -> int | None:
    path = _state_path(env)
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if value in RESERVED_PORTS or value < 1024 or value > 65535:
        return None
    return value if _health_alive_at(value) else None


def _allocate_free_port() -> int:
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        if port not in RESERVED_PORTS:
            return port
    raise RuntimeError("could not allocate a free localhost port")


def apply_portable_environment(
    env: MutableMapping[str, str] | None = None,
) -> MutableMapping[str, str]:
    target = os.environ if env is None else env
    root = portable_data_root(target)
    target.setdefault("OSAI_PROFILE", "local")
    target.setdefault("OSAI_BIND_HOST", "127.0.0.1")
    if "OSAI_PORT" not in target:
        existing = _existing_port(target) if env is None else None
        target["OSAI_PORT"] = str(existing or _allocate_free_port())
    port = int(target["OSAI_PORT"])
    if port in RESERVED_PORTS:
        raise RuntimeError(f"portable runtime may not use reserved port {port}")
    target.setdefault("OSAI_DATABASE_PATH", str(root / "data" / "osai.sqlite3"))
    target.setdefault("OSAI_SECRET_BACKEND", "os_keyring")
    target.setdefault("OSAI_NETWORK_MODE", "offline")
    return target


def _persist_runtime_port(port: int) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(port), encoding="utf-8")


def _clear_runtime_port(port: int) -> None:
    path = _state_path()
    try:
        if path.read_text(encoding="utf-8").strip() == str(port):
            path.unlink()
    except OSError:
        return


def dashboard_url() -> str:
    port = os.environ.get("OSAI_PORT")
    if not port:
        raise RuntimeError("portable runtime port is not initialized")
    return f"http://127.0.0.1:{port}/"


def setup_url() -> str:
    config = config_from_env()
    token = setup_access_token_for_runtime(config)
    from urllib.parse import quote

    return (
        f"http://127.0.0.1:{config.port}/setup#access="
        f"{quote(token, safe='')}"
    )


def _health_alive() -> bool:
    port = os.environ.get("OSAI_PORT")
    if not port:
        return False
    try:
        return _health_alive_at(int(port))
    except ValueError:
        return False


def check() -> int:
    apply_portable_environment()
    config = config_from_env()
    startup_check(config)
    report = readiness_from_env()
    print(
        json.dumps(
            {
                "app": APP_NAME,
                "profile": config.profile,
                "bind_host": config.bind_host,
                "port": config.port,
                "database_path": config.database_path,
                "data_root": str(portable_data_root()),
                "readiness": report.as_dict(),
            },
            separators=(",", ":"),
        )
    )
    return 0 if report.ready else 3


def serve() -> int:
    apply_portable_environment()
    config = config_from_env()
    startup_check(config)
    server = build_server(config)
    _persist_runtime_port(config.port)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        _clear_runtime_port(config.port)
    return 0


def run_gui() -> int:
    if os.name != "nt":
        print("Portable GUI is supported on Windows only.", file=sys.stderr)
        return 2

    apply_portable_environment()
    config = config_from_env()
    startup_check(config)

    if _health_alive():
        webbrowser.open(dashboard_url())
        return 0

    try:
        server = build_server(config)
    except OSError as exc:
        from tkinter import messagebox

        messagebox.showerror(
            APP_NAME,
            f"Could not start local Core on 127.0.0.1:{config.port}.\n\n{exc}",
        )
        return 1

    worker = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.25},
        name="osai-portable-runtime",
        daemon=True,
    )
    worker.start()
    _persist_runtime_port(config.port)

    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("470x310")
    root.resizable(False, False)

    frame = tk.Frame(root, padx=24, pady=22)
    frame.pack(fill="both", expand=True)

    tk.Label(
        frame,
        text="OS AI Core",
        font=("Segoe UI", 20, "bold"),
    ).pack(anchor="w")
    tk.Label(
        frame,
        text="Portable Local Runtime",
        font=("Segoe UI", 10),
        fg="#5f6b7a",
    ).pack(anchor="w", pady=(0, 16))

    status = tk.StringVar(value="Starting local Core...")
    tk.Label(
        frame,
        textvariable=status,
        font=("Segoe UI", 11, "bold"),
        fg="#166534",
    ).pack(anchor="w", pady=(0, 14))

    path_text = f"Data: {portable_data_root()}"
    tk.Label(
        frame,
        text=path_text,
        font=("Segoe UI", 9),
        fg="#64748b",
        wraplength=420,
        justify="left",
    ).pack(anchor="w", pady=(0, 18))

    buttons = tk.Frame(frame)
    buttons.pack(fill="x")

    def open_dashboard() -> None:
        webbrowser.open(dashboard_url())

    def open_setup() -> None:
        try:
            webbrowser.open(setup_url())
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Setup could not be opened.\n\n{exc}")

    def open_data_folder() -> None:
        root_path = portable_data_root()
        root_path.mkdir(parents=True, exist_ok=True)
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise RuntimeError("Open Data Folder is available only on Windows")
        startfile(root_path)

    tk.Button(
        buttons,
        text="Open Dashboard",
        width=18,
        command=open_dashboard,
    ).grid(row=0, column=0, padx=(0, 8), pady=4)
    tk.Button(
        buttons,
        text="Open Setup",
        width=18,
        command=open_setup,
    ).grid(row=0, column=1, padx=(0, 8), pady=4)
    tk.Button(
        buttons,
        text="Open Data Folder",
        width=18,
        command=open_data_folder,
    ).grid(row=1, column=0, padx=(0, 8), pady=4)

    stopping = False

    def stop() -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        status.set("Stopping...")
        try:
            server.shutdown()
            server.server_close()
            worker.join(timeout=10)
        finally:
            _clear_runtime_port(config.port)
            root.destroy()

    tk.Button(
        buttons,
        text="Stop OS AI Core",
        width=18,
        command=stop,
    ).grid(row=1, column=1, padx=(0, 8), pady=4)

    root.protocol("WM_DELETE_WINDOW", stop)

    def mark_ready() -> None:
        if _health_alive():
            status.set(f"Running on 127.0.0.1:{config.port}")
        else:
            status.set("Core did not become ready.")
        open_dashboard()

    root.after(700, mark_ready)
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="OS-AI-Core-Portable")
    parser.add_argument(
        "command",
        choices=("run", "check", "serve"),
        nargs="?",
        default="run",
    )
    args = parser.parse_args(argv)
    if args.command == "check":
        return check()
    if args.command == "serve":
        return serve()
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
