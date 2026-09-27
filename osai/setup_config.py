"""Local setup/configuration store for the Windows operator wizard.

Non-secret settings are stored as JSON under the runtime data root. Secrets are
stored only through a SecretStore implementation. On packaged Windows installs,
the service uses DPAPI and a file inherited from the ProgramData ACL applied by
the installer. APIs expose only whether a secret is configured, never its value.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import secrets
import tempfile
from collections.abc import Mapping
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlparse


class SetupError(RuntimeError):
    """Base class for sanitized setup failures."""


class SetupValidationError(SetupError):
    """Submitted setup data did not satisfy the local configuration contract."""


class SecretUnavailable(SetupError):
    """The requested secret is unavailable or cannot be decrypted."""


class SecretStore(Protocol):
    def set(self, name: str, value: str) -> None:
        """Persist one named secret."""

    def get(self, name: str) -> str:
        """Return one named secret or raise SecretUnavailable."""

    def delete(self, name: str) -> None:
        """Delete one named secret if it exists."""

    def configured(self, name: str) -> bool:
        """Return whether the named secret exists."""


_SECRET_NAME_RE = re.compile(r"[a-z][a-z0-9_.-]{1,63}")
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_TELEGRAM_TOKEN_RE = re.compile(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,100}")


@dataclass(frozen=True)
class SetupSettings:
    excel_enabled: bool = False
    excel_workbook_path: str | None = None
    telegram_enabled: bool = False
    telegram_bot_alias: str = "primary-bot"
    llm_provider: str = "none"
    llm_model: str | None = None
    local_llm_base_url: str | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> SetupSettings:
        allowed = {
            "excel_enabled",
            "excel_workbook_path",
            "telegram_enabled",
            "telegram_bot_alias",
            "llm_provider",
            "llm_model",
            "local_llm_base_url",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise SetupValidationError(f"unknown setup field(s): {sorted(unknown)}")

        excel_raw = raw.get("excel_enabled", False)
        if not isinstance(excel_raw, bool):
            raise SetupValidationError("excel_enabled must be boolean")
        excel_enabled = excel_raw
        workbook_raw = raw.get("excel_workbook_path")
        workbook = None if workbook_raw in {None, ""} else str(workbook_raw).strip()
        if workbook is not None:
            path = Path(workbook)
            if path.suffix.casefold() != ".xlsx":
                raise SetupValidationError("Excel workbook path must end in .xlsx")
            if not path.is_absolute():
                raise SetupValidationError("Excel workbook path must be absolute")
            workbook = str(path)

        telegram_raw = raw.get("telegram_enabled", False)
        if not isinstance(telegram_raw, bool):
            raise SetupValidationError("telegram_enabled must be boolean")
        telegram_enabled = telegram_raw
        bot_alias = str(raw.get("telegram_bot_alias", "primary-bot")).strip()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", bot_alias):
            raise SetupValidationError("telegram_bot_alias is invalid")

        provider = str(raw.get("llm_provider", "none")).strip().casefold()
        if provider not in {"none", "openai", "local"}:
            raise SetupValidationError("llm_provider must be none, openai, or local")

        model_raw = raw.get("llm_model")
        model = None if model_raw in {None, ""} else str(model_raw).strip()
        if provider == "openai":
            if not model or _MODEL_RE.fullmatch(model) is None:
                raise SetupValidationError("OpenAI provider requires a valid model name")
        elif model and _MODEL_RE.fullmatch(model) is None:
            raise SetupValidationError("llm_model is invalid")

        base_raw = raw.get("local_llm_base_url")
        base_url = None if base_raw in {None, ""} else str(base_raw).strip()
        if provider == "local":
            if not base_url:
                raise SetupValidationError("local provider requires local_llm_base_url")
            parsed = urlparse(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise SetupValidationError("local_llm_base_url must be HTTP(S)")
            host = parsed.hostname.casefold().rstrip(".")
            if host not in {"127.0.0.1", "localhost", "::1"}:
                raise SetupValidationError("local LLM URL must use loopback in Windows UAT")
            if parsed.username or parsed.password:
                raise SetupValidationError("local LLM URL cannot contain credentials")
        elif base_url:
            raise SetupValidationError("local_llm_base_url is only valid for local provider")

        return cls(
            excel_enabled=excel_enabled,
            excel_workbook_path=workbook,
            telegram_enabled=telegram_enabled,
            telegram_bot_alias=bot_alias,
            llm_provider=provider,
            llm_model=model,
            local_llm_base_url=base_url,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "excel_enabled": self.excel_enabled,
            "excel_workbook_path": self.excel_workbook_path,
            "telegram_enabled": self.telegram_enabled,
            "telegram_bot_alias": self.telegram_bot_alias,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "local_llm_base_url": self.local_llm_base_url,
        }


class JsonSetupStore:
    """Atomic JSON store for non-secret setup settings."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> SetupSettings:
        if not self.path.exists():
            return SetupSettings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SetupError("setup settings are unavailable") from exc
        if not isinstance(raw, Mapping):
            raise SetupError("setup settings are invalid")
        return SetupSettings.from_mapping(cast(Mapping[str, Any], raw))

    def save(self, settings: SetupSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".setup-",
            suffix=".json",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    settings.as_dict(),
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except OSError as exc:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise SetupError("setup settings could not be saved") from exc


class MemorySecretStore:
    """In-memory store used only by tests/source UAT."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    @staticmethod
    def _name(name: str) -> str:
        if _SECRET_NAME_RE.fullmatch(name) is None:
            raise ValueError("invalid secret name")
        return name

    def set(self, name: str, value: str) -> None:
        self._name(name)
        if not value or len(value) > 8192:
            raise SetupValidationError("secret value is empty or too large")
        self._values[name] = value

    def get(self, name: str) -> str:
        self._name(name)
        try:
            return self._values[name]
        except KeyError as exc:
            raise SecretUnavailable("secret unavailable") from exc

    def delete(self, name: str) -> None:
        self._name(name)
        self._values.pop(name, None)

    def configured(self, name: str) -> bool:
        self._name(name)
        return name in self._values


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


class WindowsDpapiSecretStore:
    """DPAPI-protected JSON secret store for the Windows service account."""

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1
    _CRYPTPROTECT_LOCAL_MACHINE = 0x4

    def __init__(self, path: str | Path, *, machine_scope: bool = True) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows DPAPI secret store is available only on Windows")
        self.path = Path(path)
        # Machine scope suits the Windows service account; the desktop app uses
        # per-user scope so only the signed-in Windows user can decrypt.
        self._flags = self._CRYPTPROTECT_UI_FORBIDDEN | (
            self._CRYPTPROTECT_LOCAL_MACHINE if machine_scope else 0
        )
        self._ctypes = cast(Any, ctypes)
        self._crypt32 = self._ctypes.WinDLL("Crypt32", use_last_error=True)
        self._kernel32 = self._ctypes.WinDLL("Kernel32", use_last_error=True)
        self._crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        self._kernel32.LocalFree.restype = wintypes.HLOCAL

    @staticmethod
    def _name(name: str) -> str:
        if _SECRET_NAME_RE.fullmatch(name) is None:
            raise ValueError("invalid secret name")
        return name

    def _protect(self, value: bytes) -> bytes:
        source_buffer = ctypes.create_string_buffer(value)
        source = _DataBlob(
            len(value),
            ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)),
        )
        output = _DataBlob()
        ok = self._crypt32.CryptProtectData(
            ctypes.byref(source),
            "OS AI Core secret",
            None,
            None,
            None,
            self._flags,
            ctypes.byref(output),
        )
        if not ok:
            raise SecretUnavailable("Windows could not protect the secret")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            self._kernel32.LocalFree(output.pbData)

    def _unprotect(self, value: bytes) -> bytes:
        source_buffer = ctypes.create_string_buffer(value)
        source = _DataBlob(
            len(value),
            ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)),
        )
        output = _DataBlob()
        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            self._flags,
            ctypes.byref(output),
        )
        if not ok:
            raise SecretUnavailable("Windows could not decrypt the secret")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            self._kernel32.LocalFree(output.pbData)

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecretUnavailable("secret store unavailable") from exc
        if not isinstance(raw, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw.items()
        ):
            raise SecretUnavailable("secret store is invalid")
        return cast(dict[str, str], raw)

    def _write(self, values: Mapping[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".secrets-",
            suffix=".json",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    dict(values),
                    handle,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except OSError as exc:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise SecretUnavailable("secret store could not be saved") from exc

    def set(self, name: str, value: str) -> None:
        self._name(name)
        if not value or len(value) > 8192:
            raise SetupValidationError("secret value is empty or too large")
        values = self._read()
        protected = self._protect(value.encode("utf-8"))
        values[name] = base64.b64encode(protected).decode("ascii")
        self._write(values)

    def get(self, name: str) -> str:
        self._name(name)
        values = self._read()
        encoded = values.get(name)
        if encoded is None:
            raise SecretUnavailable("secret unavailable")
        try:
            protected = base64.b64decode(encoded, validate=True)
            return self._unprotect(protected).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SecretUnavailable("secret unavailable") from exc

    def delete(self, name: str) -> None:
        self._name(name)
        values = self._read()
        if name in values:
            del values[name]
            self._write(values)

    def configured(self, name: str) -> bool:
        self._name(name)
        return name in self._read()


@dataclass(frozen=True)
class SetupStatus:
    settings: SetupSettings
    telegram_token_configured: bool
    openai_key_configured: bool
    setup_complete: bool

    def as_dict(self) -> dict[str, object]:
        return {
            **self.settings.as_dict(),
            "telegram_token_configured": self.telegram_token_configured,
            "openai_key_configured": self.openai_key_configured,
            "setup_complete": self.setup_complete,
        }


class SetupManager:
    """Validate and persist setup values without ever returning secrets."""

    TELEGRAM_SECRET = "telegram.bot_token"
    OPENAI_SECRET = "llm.openai_api_key"

    def __init__(self, settings_store: JsonSetupStore, secret_store: SecretStore) -> None:
        self.settings_store = settings_store
        self.secret_store = secret_store

    def status(self) -> SetupStatus:
        settings = self.settings_store.load()
        telegram_secret = self.secret_store.configured(self.TELEGRAM_SECRET)
        openai_secret = self.secret_store.configured(self.OPENAI_SECRET)
        complete = bool(
            settings.excel_enabled
            and settings.excel_workbook_path
            and (
                settings.llm_provider == "local"
                or settings.llm_provider == "none"
                or (settings.llm_provider == "openai" and openai_secret)
            )
            and (not settings.telegram_enabled or telegram_secret)
        )
        return SetupStatus(
            settings=settings,
            telegram_token_configured=telegram_secret,
            openai_key_configured=openai_secret,
            setup_complete=complete,
        )

    def apply(
        self,
        *,
        settings_raw: Mapping[str, Any],
        telegram_bot_token: str | None,
        openai_api_key: str | None,
        clear_telegram_token: bool = False,
        clear_openai_api_key: bool = False,
    ) -> SetupStatus:
        settings = SetupSettings.from_mapping(settings_raw)

        telegram_value: str | None = None
        if telegram_bot_token is not None:
            telegram_value = telegram_bot_token.strip()
            if _TELEGRAM_TOKEN_RE.fullmatch(telegram_value) is None:
                raise SetupValidationError("Telegram bot token format is invalid")

        openai_value: str | None = None
        if openai_api_key is not None:
            openai_value = openai_api_key.strip()
            if (
                len(openai_value) < 20
                or len(openai_value) > 512
                or any(ch.isspace() for ch in openai_value)
            ):
                raise SetupValidationError("OpenAI API key format is invalid")

        telegram_will_exist = telegram_value is not None or (
            self.secret_store.configured(self.TELEGRAM_SECRET) and not clear_telegram_token
        )
        openai_will_exist = openai_value is not None or (
            self.secret_store.configured(self.OPENAI_SECRET) and not clear_openai_api_key
        )
        if settings.telegram_enabled and not telegram_will_exist:
            raise SetupValidationError("Telegram is enabled but no bot token is configured")
        if settings.llm_provider == "openai" and not openai_will_exist:
            raise SetupValidationError("OpenAI is selected but no API key is configured")

        if telegram_value is not None:
            self.secret_store.set(self.TELEGRAM_SECRET, telegram_value)
        elif clear_telegram_token:
            self.secret_store.delete(self.TELEGRAM_SECRET)

        if openai_value is not None:
            self.secret_store.set(self.OPENAI_SECRET, openai_value)
        elif clear_openai_api_key:
            self.secret_store.delete(self.OPENAI_SECRET)

        self.settings_store.save(settings)
        return self.status()

    @staticmethod
    def generate_csrf_token() -> str:
        return secrets.token_urlsafe(32)
