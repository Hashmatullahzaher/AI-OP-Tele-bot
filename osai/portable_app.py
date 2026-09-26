"""One-click Windows portable host for OS AI Core.

This is a per-user local UAT host: no installer, no Windows service, and no
administrator elevation. It starts the same loopback runtime and provides a
small Tk control window with Dashboard, Setup, Data Folder, and Stop controls.
"""

from __future__ import annotations

import argparse
import json
import os
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
DEFAULT_PORT = "8765"


def portable_data_root(env: MutableMapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    base = source.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "OS AI Core Portable"


def apply_portable_environment(
    env: MutableMapping[str, str] | None = None,
) -> MutableMapping[str, str]:
    target = os.environ if env is None else env
    root = portable_data_root(target)
    target.setdefault("OSAI_PROFILE", "local")
    target.setdefault("OSAI_BIND_HOST", "127.0.0.1")
    target.setdefault("OSAI_PORT", DEFAULT_PORT)
    target.setdefault("OSAI_DATABASE_PATH", str(root / "data" / "osai.sqlite3"))
    target.setdefault("OSAI_SECRET_BACKEND", "os_keyring")
    target.setdefault("OSAI_NETWORK_MODE", "offline")
    return target


def dashboard_url() -> str:
    return f"http://127.0.0.1:{os.environ.get('OSAI_PORT', DEFAULT_PORT)}/"


def setup_url() -> str:
    config = config_from_env()
    token = setup_access_token_for_runtime(config)
    from urllib.parse import quote

    return (
        f"http://127.0.0.1:{config.port}/setup#access="
        f"{quote(token, safe='')}"
    )


def _health_alive() -> bool:
    try:
        with urllib.request.urlopen(
            f"{dashboard_url()}healthz", timeout=1
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("status") == "alive"
    except (OSError, ValueError, urllib.error.URLError):
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
        os.startfile(root_path)  # type: ignore[attr-defined]

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
        choices=("run", "check"),
        nargs="?",
        default="run",
    )
    args = parser.parse_args(argv)
    if args.command == "check":
        return check()
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
