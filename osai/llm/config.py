"""LLM connection settings: presets plus fully custom endpoints.

Environment variables (all optional except where a preset needs them):

- ``OSAI_LLM_PRESET``: one of :data:`PRESETS` (default ``openrouter``) or ``custom``.
- ``OSAI_LLM_PROTOCOL``: ``openai_compatible`` or ``anthropic`` (overrides preset).
- ``OSAI_LLM_BASE_URL``: API base URL (overrides preset).
- ``OSAI_LLM_MODEL``: model name (overrides preset default).
- ``OSAI_LLM_AUTH``: ``api_key`` | ``header`` | ``none`` | ``command``.
- ``OSAI_LLM_API_KEY``: key for ``api_key``/``header`` auth.
- ``OSAI_LLM_AUTH_HEADER`` / ``OSAI_LLM_AUTH_PREFIX``: custom header name and value prefix.
- ``OSAI_LLM_TOKEN_COMMAND`` / ``OSAI_LLM_TOKEN_TTL``: sign-in token command and cache seconds.
- ``OSAI_LLM_EXTRA_HEADERS``: ``Name=value;Name2=value2`` non-secret headers.
- ``OSAI_LLM_TIMEOUT``: request timeout seconds (default 60).
- ``OSAI_LLM_JSON_MODE``: ``1`` to request ``response_format=json_object`` (default on
  when the preset supports it).
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field

from .auth import CommandTokenCredential, Credential, CredentialError, NoCredential, StaticCredential

PROTOCOLS = frozenset({"openai_compatible", "anthropic"})
AUTH_MODES = frozenset({"api_key", "header", "none", "command"})


@dataclass(frozen=True)
class Preset:
    protocol: str
    base_url: str
    default_model: str | None
    auth: str
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    json_mode: bool = True
    extra_headers: Mapping[str, str] = field(default_factory=dict)


PRESETS: Mapping[str, Preset] = {
    "openrouter": Preset(
        "openai_compatible",
        "https://openrouter.ai/api/v1",
        None,
        "api_key",
        extra_headers={"X-Title": "OS AI Core"},
    ),
    "openai": Preset("openai_compatible", "https://api.openai.com/v1", None, "api_key"),
    "groq": Preset("openai_compatible", "https://api.groq.com/openai/v1", None, "api_key"),
    "gemini": Preset(
        "openai_compatible",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        None,
        "api_key",
    ),
    "deepseek": Preset("openai_compatible", "https://api.deepseek.com/v1", None, "api_key"),
    "mistral": Preset("openai_compatible", "https://api.mistral.ai/v1", None, "api_key"),
    "together": Preset("openai_compatible", "https://api.together.xyz/v1", None, "api_key"),
    "ollama": Preset("openai_compatible", "http://127.0.0.1:11434/v1", None, "none", json_mode=False),
    "lmstudio": Preset("openai_compatible", "http://127.0.0.1:1234/v1", None, "none", json_mode=False),
    "anthropic": Preset("anthropic", "https://api.anthropic.com", "claude-opus-5", "api_key"),
}


class LLMConfigError(ValueError):
    """Invalid LLM configuration (message never contains secret values)."""


@dataclass(frozen=True)
class LLMSettings:
    protocol: str
    base_url: str
    model: str
    credential: Credential
    json_mode: bool = True
    timeout_seconds: int = 60
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    provider_label: str = "custom"

    def __post_init__(self) -> None:
        if self.protocol not in PROTOCOLS:
            raise LLMConfigError("unsupported LLM protocol")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise LLMConfigError("LLM base URL must be an http(s) URL without embedded credentials")
        if (
            parsed.scheme == "http"
            and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            and not (parsed.hostname.endswith(".internal") or parsed.hostname.endswith(".local"))
        ):
            raise LLMConfigError("plain http is only allowed for local model servers")
        if not self.model or len(self.model) > 200 or any(ch.isspace() for ch in self.model):
            raise LLMConfigError("LLM model name is missing or invalid")
        if self.timeout_seconds < 5 or self.timeout_seconds > 600:
            raise LLMConfigError("LLM timeout out of range")


def _parse_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in raw.split(";"):
        if not item.strip():
            continue
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            raise LLMConfigError("OSAI_LLM_EXTRA_HEADERS must be Name=value;Name=value")
        headers[name.strip()] = value.strip()
    return headers


def settings_from_env(env: Mapping[str, str]) -> LLMSettings:
    preset_name = env.get("OSAI_LLM_PRESET", "openrouter").strip().lower() or "openrouter"
    if preset_name == "custom":
        preset = Preset("openai_compatible", "", None, "api_key")
    elif preset_name in PRESETS:
        preset = PRESETS[preset_name]
    else:
        raise LLMConfigError(f"unknown OSAI_LLM_PRESET; choose one of {', '.join(sorted(PRESETS))} or custom")

    protocol = env.get("OSAI_LLM_PROTOCOL", "").strip() or preset.protocol
    base_url = (env.get("OSAI_LLM_BASE_URL", "").strip() or preset.base_url).rstrip("/")
    model = env.get("OSAI_LLM_MODEL", "").strip() or (preset.default_model or "")
    auth = env.get("OSAI_LLM_AUTH", "").strip() or preset.auth
    if auth not in AUTH_MODES:
        raise LLMConfigError("OSAI_LLM_AUTH must be api_key, header, none or command")
    header = env.get("OSAI_LLM_AUTH_HEADER", "").strip()
    prefix_raw = env.get("OSAI_LLM_AUTH_PREFIX")

    credential: Credential
    try:
        if auth == "none":
            credential = NoCredential()
        elif auth == "command":
            credential = CommandTokenCredential(
                command=env.get("OSAI_LLM_TOKEN_COMMAND", ""),
                ttl_seconds=int(env.get("OSAI_LLM_TOKEN_TTL", "300")),
                header=header or preset.auth_header,
                prefix=preset.auth_prefix if prefix_raw is None else prefix_raw,
            )
        else:
            key = env.get("OSAI_LLM_API_KEY", "").strip()
            if auth == "header":
                if not header:
                    raise LLMConfigError("OSAI_LLM_AUTH_HEADER is required for header auth")
                credential = StaticCredential(key, header=header, prefix=prefix_raw or "")
            else:
                credential = StaticCredential(
                    key,
                    header=header or preset.auth_header,
                    prefix=preset.auth_prefix if prefix_raw is None else prefix_raw,
                )
    except CredentialError as exc:
        raise LLMConfigError(f"LLM credential configuration invalid: {exc}") from exc
    except ValueError as exc:
        raise LLMConfigError("OSAI_LLM_TOKEN_TTL must be an integer") from exc

    json_raw = env.get("OSAI_LLM_JSON_MODE", "").strip()
    json_mode = preset.json_mode if not json_raw else json_raw in {"1", "true", "yes"}
    extra = dict(preset.extra_headers)
    extra.update(_parse_headers(env.get("OSAI_LLM_EXTRA_HEADERS", "")))
    try:
        timeout = int(env.get("OSAI_LLM_TIMEOUT", "60"))
    except ValueError as exc:
        raise LLMConfigError("OSAI_LLM_TIMEOUT must be an integer") from exc
    if not base_url:
        raise LLMConfigError("OSAI_LLM_BASE_URL is required for a custom endpoint")
    return LLMSettings(
        protocol=protocol,
        base_url=base_url,
        model=model,
        credential=credential,
        json_mode=json_mode,
        timeout_seconds=timeout,
        extra_headers=extra,
        provider_label=preset_name,
    )
