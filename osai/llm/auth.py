"""Credential sources for LLM connections.

Secrets are read from the server environment at runtime and never logged,
persisted or sent to the model.
"""

from __future__ import annotations

import shlex
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol


class CredentialError(RuntimeError):
    """The configured credential could not be obtained."""


class Credential(Protocol):
    def headers(self) -> Mapping[str, str]:
        """HTTP headers that authenticate one request."""

    def secret(self) -> str | None:
        """Raw key/token for SDKs that take it directly (None when no auth)."""


@dataclass(frozen=True)
class NoCredential:
    """Local models (Ollama, LM Studio, vLLM) that need no authentication."""

    def headers(self) -> Mapping[str, str]:
        return {}

    def secret(self) -> str | None:
        return None


@dataclass(frozen=True)
class StaticCredential:
    """An API key sent as ``Authorization: Bearer`` or in a custom header."""

    value: str
    header: str = "Authorization"
    prefix: str = "Bearer "

    def __post_init__(self) -> None:
        if not self.value or any(ch.isspace() for ch in self.value):
            raise CredentialError("API key is empty or malformed")
        if not self.header or any(ch.isspace() or ch == ":" for ch in self.header):
            raise CredentialError("auth header name is invalid")

    def headers(self) -> Mapping[str, str]:
        return {self.header: f"{self.prefix}{self.value}"}

    def secret(self) -> str | None:
        return self.value

    def __repr__(self) -> str:
        return f"StaticCredential(header={self.header!r}, value=<redacted>)"


Runner = Callable[[list[str], float], str]


def _run_command(argv: list[str], timeout: float) -> str:
    completed = subprocess.run(  # noqa: S603 - argv is operator config, no shell
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise CredentialError("token command failed")
    return completed.stdout


@dataclass
class CommandTokenCredential:
    """Sign-in based auth: run an operator-configured command that prints a
    short-lived OAuth access token (e.g. ``gcloud auth print-access-token`` or
    ``az account get-access-token --query accessToken -o tsv``). The token is
    cached for ``ttl_seconds`` and refreshed on expiry. No shell is used.
    """

    command: str
    ttl_seconds: int = 300
    header: str = "Authorization"
    prefix: str = "Bearer "
    timeout_seconds: float = 30.0
    runner: Runner = _run_command
    _token: str | None = field(default=None, init=False, repr=False)
    _expires_at: float = field(default=0.0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not shlex.split(self.command):
            raise CredentialError("token command is empty")
        if self.ttl_seconds < 30 or self.ttl_seconds > 3_600:
            raise CredentialError("token ttl out of range")

    def _current(self) -> str:
        with self._lock:
            now = time.monotonic()
            if self._token is None or now >= self._expires_at:
                raw = self.runner(shlex.split(self.command), self.timeout_seconds).strip()
                if not raw or any(ch.isspace() for ch in raw) or len(raw) > 16_384:
                    raise CredentialError("token command returned an invalid token")
                self._token = raw
                self._expires_at = now + self.ttl_seconds
            return self._token

    def headers(self) -> Mapping[str, str]:
        return {self.header: f"{self.prefix}{self._current()}"}

    def secret(self) -> str | None:
        return self._current()
