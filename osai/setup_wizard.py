"""Loopback-only setup wizard controller for the local Windows runtime."""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .connectors.local_excel import initialize_excel_sandbox
from .setup_config import (
    JsonSetupStore,
    MemorySecretStore,
    SetupError,
    SetupManager,
    SetupStatus,
    SetupValidationError,
    WindowsDpapiSecretStore,
)
from .setup_page import render_setup_page


def runtime_data_root(config: RuntimeConfig) -> Path:
    db_path = Path(config.database_path).resolve()
    parent = db_path.parent
    if parent.name.casefold() == "data":
        return parent.parent
    return parent


def setup_manager_for_runtime(config: RuntimeConfig) -> SetupManager:
    root = runtime_data_root(config)
    settings_store = JsonSetupStore(root / "config" / "settings.json")
    if config.secret_backend == "test":
        secret_store = MemorySecretStore()
    elif config.secret_backend == "os_keyring":
        secret_store = WindowsDpapiSecretStore(root / "config" / "secrets.dpapi.json")
    else:
        raise SetupError("managed secret backend is not available in local setup wizard")
    return SetupManager(settings_store, secret_store)


@dataclass(frozen=True)
class SetupApplyRequest:
    settings: Mapping[str, Any]
    telegram_bot_token: str | None
    openai_api_key: str | None
    clear_telegram_token: bool
    clear_openai_api_key: bool

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SetupApplyRequest":
        allowed = {
            "settings",
            "telegram_bot_token",
            "openai_api_key",
            "clear_telegram_token",
            "clear_openai_api_key",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise SetupValidationError(f"unknown setup request field(s): {sorted(unknown)}")
        settings = raw.get("settings")
        if not isinstance(settings, Mapping) or not all(isinstance(k, str) for k in settings):
            raise SetupValidationError("settings must be an object")

        def optional_secret(name: str) -> str | None:
            value = raw.get(name)
            if value in {None, ""}:
                return None
            if not isinstance(value, str):
                raise SetupValidationError(f"{name} must be a string")
            return value

        return cls(
            settings=dict(settings),
            telegram_bot_token=optional_secret("telegram_bot_token"),
            openai_api_key=optional_secret("openai_api_key"),
            clear_telegram_token=bool(raw.get("clear_telegram_token", False)),
            clear_openai_api_key=bool(raw.get("clear_openai_api_key", False)),
        )


class SetupWebController:
    """Stateful controller; the CSRF token lives only in service memory."""

    def __init__(self, *, config: RuntimeConfig, manager: SetupManager) -> None:
        self.config = config
        self.manager = manager
        self.csrf_token = manager.generate_csrf_token()

    @property
    def managed_excel_path(self) -> Path:
        return runtime_data_root(self.config) / "data" / "OS-AI-Core-Excel-UAT.xlsx"

    def status(self) -> dict[str, object]:
        return self._status_payload(self.manager.status())

    def apply(self, raw: Mapping[str, Any]) -> dict[str, object]:
        request = SetupApplyRequest.from_mapping(raw)
        status = self.manager.apply(
            settings_raw=request.settings,
            telegram_bot_token=request.telegram_bot_token,
            openai_api_key=request.openai_api_key,
            clear_telegram_token=request.clear_telegram_token,
            clear_openai_api_key=request.clear_openai_api_key,
        )
        return self._status_payload(status)

    def initialize_excel(self) -> dict[str, object]:
        path = self.managed_excel_path
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            initialize_excel_sandbox(path)
        current = self.manager.status().settings.as_dict()
        current["excel_enabled"] = True
        current["excel_workbook_path"] = str(path)
        status = self.manager.apply(
            settings_raw=current,
            telegram_bot_token=None,
            openai_api_key=None,
        )
        return self._status_payload(status)

    def verify_csrf(self, presented: str | None) -> bool:
        return presented is not None and hmac.compare_digest(self.csrf_token, presented)

    def render(self) -> str:
        return render_setup_page(self.status(), self.csrf_token)

    def _status_payload(self, status: SetupStatus) -> dict[str, object]:
        payload = status.as_dict()
        payload["managed_excel_path"] = str(self.managed_excel_path)
        payload["managed_excel_exists"] = self.managed_excel_path.exists()
        payload["telegram_connector_active"] = False
        payload["llm_connector_active"] = False
        payload["live_activation_note"] = (
            "Credentials can be stored now; live Telegram and LLM activation is a separate gated stage."
        )
        return payload
