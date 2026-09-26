"""Windows service host for the local OS AI Core runtime.

The service uses only the Python standard library and Win32 APIs exposed through
``ctypes``. It deliberately keeps the local profile bound to loopback and stores
mutable runtime data under ProgramData instead of the installation directory.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
from collections.abc import MutableMapping
from ctypes import wintypes
from pathlib import Path

from .runtime import build_server, config_from_env, readiness_from_env, startup_check

SERVICE_NAME = "OSAICore"
SERVICE_DISPLAY_NAME = "OS AI Core"
SERVICE_DESCRIPTION = "OS AI Core local runtime"

SERVICE_WIN32_OWN_PROCESS = 0x00000010
SERVICE_START_PENDING = 0x00000002
SERVICE_STOP_PENDING = 0x00000003
SERVICE_RUNNING = 0x00000004
SERVICE_STOPPED = 0x00000001
SERVICE_ACCEPT_STOP = 0x00000001
SERVICE_ACCEPT_SHUTDOWN = 0x00000004
SERVICE_CONTROL_STOP = 0x00000001
SERVICE_CONTROL_SHUTDOWN = 0x00000005
NO_ERROR = 0
ERROR_FAILED_SERVICE_CONTROLLER_CONNECT = 1063


class _ServiceStatus(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wintypes.DWORD),
        ("dwCurrentState", wintypes.DWORD),
        ("dwControlsAccepted", wintypes.DWORD),
        ("dwWin32ExitCode", wintypes.DWORD),
        ("dwServiceSpecificExitCode", wintypes.DWORD),
        ("dwCheckPoint", wintypes.DWORD),
        ("dwWaitHint", wintypes.DWORD),
    ]


def program_data_root(env: MutableMapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    base = source.get("PROGRAMDATA") or source.get("ProgramData")
    if not base:
        base = str(Path.home() / ".os-ai-core")
    return Path(base) / "OS AI Core"


def apply_service_environment(env: MutableMapping[str, str] | None = None) -> MutableMapping[str, str]:
    target = os.environ if env is None else env
    root = program_data_root(target)
    target.setdefault("OSAI_PROFILE", "local")
    target.setdefault("OSAI_BIND_HOST", "127.0.0.1")
    target.setdefault("OSAI_PORT", "8765")
    target.setdefault("OSAI_DATABASE_PATH", str(root / "data" / "osai.sqlite3"))
    target.setdefault("OSAI_SECRET_BACKEND", "os_keyring")
    target.setdefault("OSAI_NETWORK_MODE", "offline")
    return target


def _service_log(message: str) -> None:
    try:
        path = program_data_root() / "logs" / "service.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(message.replace("\r", " ").replace("\n", " ")[:500] + "\n")
    except OSError:
        return


def _check() -> int:
    apply_service_environment()
    config = config_from_env()
    startup_check(config)
    report = readiness_from_env()
    print(
        json.dumps(
            {
                "service": SERVICE_NAME,
                "profile": config.profile,
                "bind_host": config.bind_host,
                "port": config.port,
                "database_path": config.database_path,
                "readiness": report.as_dict(),
            },
            separators=(",", ":"),
        )
    )
    return 0 if report.ready else 3


def _console() -> int:
    apply_service_environment()
    config = config_from_env()
    startup_check(config)
    server = build_server(config)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _run_service_dispatcher() -> int:
    if os.name != "nt":
        print("Windows service mode is available only on Windows.", file=sys.stderr)
        return 2

    advapi32 = ctypes.WinDLL("Advapi32", use_last_error=True)  # type: ignore[attr-defined]
    winfunctype = ctypes.WINFUNCTYPE  # type: ignore[attr-defined]
    handler_type = winfunctype(
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    )
    service_main_type = winfunctype(None, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR))

    class _ServiceTableEntry(ctypes.Structure):
        _fields_ = [("lpServiceName", wintypes.LPWSTR), ("lpServiceProc", service_main_type)]

    advapi32.RegisterServiceCtrlHandlerExW.argtypes = [wintypes.LPCWSTR, handler_type, wintypes.LPVOID]
    advapi32.RegisterServiceCtrlHandlerExW.restype = wintypes.HANDLE
    advapi32.SetServiceStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ServiceStatus)]
    advapi32.SetServiceStatus.restype = wintypes.BOOL
    advapi32.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(_ServiceTableEntry)]
    advapi32.StartServiceCtrlDispatcherW.restype = wintypes.BOOL

    stop_event = threading.Event()
    status_handle: wintypes.HANDLE | None = None
    checkpoint = 0

    def report_status(state: int, *, exit_code: int = 0, wait_hint: int = 0) -> None:
        nonlocal checkpoint
        if status_handle is None:
            return
        if state in {SERVICE_START_PENDING, SERVICE_STOP_PENDING}:
            checkpoint += 1
        else:
            checkpoint = 0
        controls = 0
        if state == SERVICE_RUNNING:
            controls = SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN
        status = _ServiceStatus(
            SERVICE_WIN32_OWN_PROCESS,
            state,
            controls,
            exit_code,
            0,
            checkpoint,
            wait_hint,
        )
        advapi32.SetServiceStatus(status_handle, ctypes.byref(status))

    @handler_type
    def control_handler(
        control: int,
        event_type: int,
        event_data: wintypes.LPVOID,
        context: wintypes.LPVOID,
    ) -> int:
        del event_type, event_data, context
        if control in {SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN}:
            report_status(SERVICE_STOP_PENDING, wait_hint=15_000)
            stop_event.set()
        return NO_ERROR

    @service_main_type
    def service_main(argc: int, argv: ctypes.POINTER(wintypes.LPWSTR)) -> None:
        nonlocal status_handle
        del argc, argv
        status_handle = advapi32.RegisterServiceCtrlHandlerExW(SERVICE_NAME, control_handler, None)
        if not status_handle:
            return
        report_status(SERVICE_START_PENDING, wait_hint=15_000)
        server = None
        worker = None
        exit_code = 0
        try:
            apply_service_environment()
            config = config_from_env()
            startup_check(config)
            server = build_server(config)
            worker = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.25},
                name="osai-http-runtime",
                daemon=True,
            )
            worker.start()
            report_status(SERVICE_RUNNING)
            _service_log("service_started")
            stop_event.wait()
        except Exception as exc:  # pragma: no cover - exercised by Windows installer smoke
            exit_code = 1
            _service_log(f"service_error:{type(exc).__name__}")
        finally:
            report_status(SERVICE_STOP_PENDING, wait_hint=15_000)
            if server is not None:
                server.shutdown()
                server.server_close()
            if worker is not None:
                worker.join(timeout=15)
            report_status(SERVICE_STOPPED, exit_code=exit_code)
            _service_log("service_stopped")

    table = (_ServiceTableEntry * 2)()
    table[0].lpServiceName = SERVICE_NAME
    table[0].lpServiceProc = service_main
    table[1].lpServiceName = None
    table[1].lpServiceProc = service_main_type()
    ok = advapi32.StartServiceCtrlDispatcherW(table)
    if ok:
        return 0
    error = ctypes.get_last_error()
    if error == ERROR_FAILED_SERVICE_CONTROLLER_CONNECT:
        print("This command must be launched by the Windows Service Control Manager.", file=sys.stderr)
    else:
        print(f"StartServiceCtrlDispatcher failed with Win32 error {error}.", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="OS-AI-Core-Service")
    parser.add_argument("command", choices=("service", "console", "check"), nargs="?", default="check")
    args = parser.parse_args(argv)
    if args.command == "service":
        return _run_service_dispatcher()
    if args.command == "console":
        return _console()
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())
