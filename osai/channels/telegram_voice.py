"""Telegram voice-note extension for OS AI Core F7.

The voice path preserves the F2 security invariants:
- webhook secret verification happens before update claiming;
- only private chats are accepted;
- update replay is denied through the shared tenant store;
- the Telegram chat must already be paired to an active tenant actor;
- audio is fetched only after identity re-check, with bounded size and safe path rules;
- no raw audio, file path, bot token, or transcript is persisted by this module.
"""

from __future__ import annotations

import hmac
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn, Protocol

from ..contracts import ExecutionContext, PolicyDenied
from ..storage import TenantSecurityStore
from .telegram import (
    BotTokenProvider,
    SecurityEventSink,
    TelegramAccessDenied,
    TelegramIngress,
    TelegramIngressResult,
    TelegramUnavailable,
    WebhookSecretProvider,
)


@dataclass(frozen=True)
class TelegramVoiceIngressResult:
    kind: str
    context: ExecutionContext
    chat_id: int
    telegram_user_id: int
    message_id: int
    update_id: int
    file_id: str
    duration_seconds: int
    mime_type: str
    declared_size_bytes: int | None = None


class TelegramVoiceTransport(Protocol):
    def post_json(
        self,
        url: str,
        *,
        payload: Mapping[str, Any],
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        """POST one Telegram JSON request."""

    def get_bytes(
        self,
        url: str,
        *,
        timeout_seconds: int,
        max_bytes: int,
    ) -> bytes:
        """GET one bounded Telegram media file."""


class UrllibTelegramVoiceTransport:
    """Telegram-only HTTPS transport with redirects disabled."""

    _HOST = "api.telegram.org"

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise TelegramAccessDenied("telegram redirect refused")

    def __init__(self, *, max_json_bytes: int = 1_000_000) -> None:
        if max_json_bytes < 1 or max_json_bytes > 5_000_000:
            raise ValueError("max_json_bytes out of range")
        self.max_json_bytes = max_json_bytes
        self._opener = urllib.request.build_opener(self._NoRedirect())

    def _request(self, url: str, *, body: bytes | None = None) -> urllib.request.Request:
        parsed = urllib.parse.urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != self._HOST
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
        ):
            raise TelegramAccessDenied("telegram host is not allowed")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        return urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method="POST" if body is not None else "GET",
        )

    def post_json(
        self,
        url: str,
        *,
        payload: Mapping[str, Any],
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request = self._request(url, body=body)
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                raw = response.read(self.max_json_bytes + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403, 404}:
                raise TelegramAccessDenied("telegram media unavailable") from exc
            if exc.code == 429 or 500 <= exc.code <= 599:
                raise TelegramUnavailable("telegram API unavailable") from exc
            raise TelegramUnavailable("telegram API request failed") from exc
        except urllib.error.URLError as exc:
            raise TelegramUnavailable("telegram API unavailable") from exc
        if len(raw) > self.max_json_bytes:
            raise TelegramUnavailable("telegram response exceeded configured limit")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TelegramUnavailable("telegram returned invalid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise TelegramUnavailable("telegram returned an unexpected response")
        return decoded

    def get_bytes(self, url: str, *, timeout_seconds: int, max_bytes: int) -> bytes:
        request = self._request(url)
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                payload = response.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403, 404}:
                raise TelegramAccessDenied("telegram media unavailable") from exc
            if exc.code == 429 or 500 <= exc.code <= 599:
                raise TelegramUnavailable("telegram API unavailable") from exc
            raise TelegramUnavailable("telegram media request failed") from exc
        except urllib.error.URLError as exc:
            raise TelegramUnavailable("telegram API unavailable") from exc
        if len(payload) > max_bytes:
            raise TelegramAccessDenied("telegram voice file exceeded configured limit")
        if not payload:
            raise TelegramUnavailable("telegram returned an empty voice file")
        return payload


class TelegramVoiceIngress:
    """Validate and route one private Telegram voice-note update."""

    _SECRET_HEADER = "x-telegram-bot-api-secret-token"
    _ALLOWED_MIMES = frozenset({"audio/ogg", "application/ogg"})

    def __init__(
        self,
        *,
        store: TenantSecurityStore,
        secret_provider: WebhookSecretProvider,
        security_sink: SecurityEventSink,
        max_body_bytes: int = 256_000,
        max_duration_seconds: int = 300,
        max_declared_bytes: int = 20_000_000,
    ) -> None:
        if max_body_bytes < 1 or max_body_bytes > 2_000_000:
            raise ValueError("max_body_bytes out of range")
        if max_duration_seconds < 1 or max_duration_seconds > 900:
            raise ValueError("max_duration_seconds out of range")
        if max_declared_bytes < 1 or max_declared_bytes > 50_000_000:
            raise ValueError("max_declared_bytes out of range")
        self.store = store
        self.secret_provider = secret_provider
        self.security_sink = security_sink
        self.max_body_bytes = max_body_bytes
        self.max_duration_seconds = max_duration_seconds
        self.max_declared_bytes = max_declared_bytes

    def handle(
        self,
        *,
        bot_alias: str,
        headers: Mapping[str, str],
        body: bytes,
        now_epoch: int,
    ) -> TelegramVoiceIngressResult:
        if now_epoch < 0:
            raise TelegramAccessDenied("telegram request denied")
        _validate_bot_alias(bot_alias)
        self._verify_webhook_secret(bot_alias, headers)
        update = self._parse_update(body)
        update_id = _required_int(update, "update_id", minimum=0)
        try:
            self.store.claim_telegram_update(
                bot_alias=bot_alias,
                update_id=update_id,
                received_at=now_epoch,
            )
        except PolicyDenied as exc:
            self.security_sink.emit(
                event="telegram.replay_denied",
                metadata={"bot_alias": bot_alias, "update_id": update_id},
            )
            raise TelegramAccessDenied("telegram update replayed") from exc

        message = update.get("message")
        if not isinstance(message, Mapping):
            self._deny("telegram.unsupported_update", bot_alias, update_id)
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, Mapping) or not isinstance(sender, Mapping):
            self._deny("telegram.invalid_message", bot_alias, update_id)
        if chat.get("type") != "private":
            self._deny("telegram.group_denied", bot_alias, update_id)
        chat_id = _required_int(chat, "id", minimum=1)
        telegram_user_id = _required_int(sender, "id", minimum=1)
        message_id = _required_int(message, "message_id", minimum=1)

        try:
            binding = self.store.resolve_telegram_binding(
                bot_alias=bot_alias,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
            )
        except PolicyDenied as exc:
            self.security_sink.emit(
                event="telegram.unpaired_denied",
                metadata={"bot_alias": bot_alias, "update_id": update_id},
            )
            raise TelegramAccessDenied("telegram identity unavailable") from exc

        voice = message.get("voice")
        if not isinstance(voice, Mapping):
            self._deny("telegram.non_voice_denied", bot_alias, update_id)
        file_id = voice.get("file_id")
        if not isinstance(file_id, str) or not _safe_file_id(file_id):
            self._deny("telegram.invalid_voice", bot_alias, update_id)
        duration = _required_int(voice, "duration", minimum=1)
        if duration > self.max_duration_seconds:
            self._deny("telegram.voice_too_long", bot_alias, update_id)
        declared_size = voice.get("file_size")
        if declared_size is not None:
            if (
                isinstance(declared_size, bool)
                or not isinstance(declared_size, int)
                or declared_size < 1
            ):
                self._deny("telegram.invalid_voice", bot_alias, update_id)
            if declared_size > self.max_declared_bytes:
                self._deny("telegram.voice_too_large", bot_alias, update_id)
        mime = voice.get("mime_type", "audio/ogg")
        if not isinstance(mime, str) or mime not in self._ALLOWED_MIMES:
            self._deny("telegram.invalid_voice_mime", bot_alias, update_id)

        context = ExecutionContext.issue(
            tenant_id=binding.tenant_id,
            actor_id=binding.actor_id,
            request_classification="internal",
            session_assurance="telegram-paired",
        )
        self.store.append_audit(
            context=context,
            event="telegram.voice.accepted",
            result="OK",
            resource_scope=f"telegram:{bot_alias}",
            sensitive_payload={
                "update_id": update_id,
                "message_id": message_id,
                "duration_seconds": duration,
                "declared_size_bytes": declared_size,
            },
        )
        self.security_sink.emit(
            event="telegram.voice.accepted",
            metadata={"bot_alias": bot_alias, "update_id": update_id},
        )
        return TelegramVoiceIngressResult(
            kind="voice",
            context=context,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            message_id=message_id,
            update_id=update_id,
            file_id=file_id,
            duration_seconds=duration,
            mime_type=mime,
            declared_size_bytes=declared_size,
        )

    def _verify_webhook_secret(self, bot_alias: str, headers: Mapping[str, str]) -> None:
        expected = self.secret_provider.webhook_secret(bot_alias=bot_alias)
        normalized = {str(key).casefold(): str(value) for key, value in headers.items()}
        presented = normalized.get(self._SECRET_HEADER, "")
        if (
            not expected
            or len(expected) > 256
            or not re.fullmatch(r"[A-Za-z0-9_-]+", expected)
            or not presented
            or not hmac.compare_digest(expected, presented)
        ):
            self.security_sink.emit(
                event="telegram.invalid_webhook",
                metadata={"bot_alias": bot_alias},
            )
            raise TelegramAccessDenied("telegram webhook denied")

    def _parse_update(self, body: bytes) -> Mapping[str, Any]:
        if not body or len(body) > self.max_body_bytes:
            raise TelegramAccessDenied("telegram request denied")
        try:
            parsed = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise TelegramAccessDenied("telegram request denied") from exc
        if not isinstance(parsed, Mapping):
            raise TelegramAccessDenied("telegram request denied")
        return parsed

    def _deny(self, event: str, bot_alias: str, update_id: int) -> NoReturn:
        self.security_sink.emit(
            event=event,
            metadata={"bot_alias": bot_alias, "update_id": update_id},
        )
        raise TelegramAccessDenied("telegram request denied")


class TelegramChannelRouter:
    """Select exactly one F2/F7 ingress path for a Telegram webhook update."""

    def __init__(
        self,
        *,
        text_ingress: TelegramIngress,
        voice_ingress: TelegramVoiceIngress,
        max_body_bytes: int = 256_000,
    ) -> None:
        self.text_ingress = text_ingress
        self.voice_ingress = voice_ingress
        self.max_body_bytes = max_body_bytes

    def handle(
        self,
        *,
        bot_alias: str,
        headers: Mapping[str, str],
        body: bytes,
        now_epoch: int,
    ) -> TelegramIngressResult | TelegramVoiceIngressResult:
        if not body or len(body) > self.max_body_bytes:
            raise TelegramAccessDenied("telegram request denied")
        try:
            update = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise TelegramAccessDenied("telegram request denied") from exc
        if not isinstance(update, Mapping):
            raise TelegramAccessDenied("telegram request denied")
        message = update.get("message")
        if not isinstance(message, Mapping):
            raise TelegramAccessDenied("telegram request denied")
        has_text = isinstance(message.get("text"), str)
        has_voice = isinstance(message.get("voice"), Mapping)
        if has_text == has_voice:
            raise TelegramAccessDenied("telegram request denied")
        if has_voice:
            return self.voice_ingress.handle(
                bot_alias=bot_alias,
                headers=headers,
                body=body,
                now_epoch=now_epoch,
            )
        return self.text_ingress.handle(
            bot_alias=bot_alias,
            headers=headers,
            body=body,
            now_epoch=now_epoch,
        )


class TelegramVoiceMediaClient:
    """Resolve and download one already-authorized Telegram voice file."""

    def __init__(
        self,
        *,
        store: TenantSecurityStore,
        token_provider: BotTokenProvider,
        transport: TelegramVoiceTransport | None = None,
        timeout_seconds: int = 20,
        max_download_bytes: int = 20_000_000,
    ) -> None:
        if timeout_seconds < 1 or timeout_seconds > 60:
            raise ValueError("timeout_seconds out of range")
        if max_download_bytes < 1 or max_download_bytes > 50_000_000:
            raise ValueError("max_download_bytes out of range")
        self.store = store
        self.token_provider = token_provider
        self.transport = transport or UrllibTelegramVoiceTransport()
        self.timeout_seconds = timeout_seconds
        self.max_download_bytes = max_download_bytes

    def fetch(
        self,
        *,
        bot_alias: str,
        ingress: TelegramVoiceIngressResult,
    ) -> bytes:
        _validate_bot_alias(bot_alias)
        try:
            binding = self.store.resolve_telegram_binding(
                bot_alias=bot_alias,
                telegram_user_id=ingress.telegram_user_id,
                chat_id=ingress.chat_id,
            )
        except PolicyDenied as exc:
            raise TelegramAccessDenied("telegram voice identity unavailable") from exc
        if (
            binding.tenant_id != ingress.context.tenant_id
            or binding.actor_id != ingress.context.actor_id
        ):
            raise TelegramAccessDenied("telegram voice identity unavailable")
        token = self.token_provider.bot_token(bot_alias=bot_alias)
        if not re.fullmatch(r"[0-9]{1,20}:[A-Za-z0-9_-]{20,200}", token):
            raise TelegramAccessDenied("telegram bot credential unavailable")
        encoded_token = urllib.parse.quote(token, safe=":")
        get_file_url = f"https://api.telegram.org/bot{encoded_token}/getFile"
        response = self.transport.post_json(
            get_file_url,
            payload={"file_id": ingress.file_id},
            timeout_seconds=self.timeout_seconds,
        )
        if response.get("ok") is not True:
            raise TelegramUnavailable("telegram voice lookup failed")
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise TelegramUnavailable("telegram voice lookup returned invalid result")
        path = result.get("file_path")
        if not isinstance(path, str) or not _safe_file_path(path):
            raise TelegramAccessDenied("telegram voice path unavailable")
        api_size = result.get("file_size")
        if api_size is not None:
            if (
                isinstance(api_size, bool)
                or not isinstance(api_size, int)
                or api_size < 1
            ):
                raise TelegramUnavailable("telegram voice size metadata invalid")
            if api_size > self.max_download_bytes:
                raise TelegramAccessDenied("telegram voice file exceeded configured limit")
        encoded_path = "/".join(
            urllib.parse.quote(segment, safe="") for segment in path.split("/")
        )
        file_url = f"https://api.telegram.org/file/bot{encoded_token}/{encoded_path}"
        payload = self.transport.get_bytes(
            file_url,
            timeout_seconds=self.timeout_seconds,
            max_bytes=self.max_download_bytes,
        )
        self.store.append_audit(
            context=ingress.context,
            event="telegram.voice.downloaded",
            result="OK",
            resource_scope=f"telegram:{bot_alias}",
            sensitive_payload={
                "bytes": len(payload),
                "duration_seconds": ingress.duration_seconds,
            },
        )
        return payload


def _validate_bot_alias(bot_alias: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", bot_alias):
        raise TelegramAccessDenied("telegram bot unavailable")


def _required_int(source: Mapping[str, Any], key: str, *, minimum: int) -> int:
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TelegramAccessDenied("telegram request denied")
    return value


def _safe_file_id(value: str) -> bool:
    return 1 <= len(value) <= 512 and not any(
        ord(ch) < 33 or ch.isspace() for ch in value
    )


def _safe_file_path(path: str) -> bool:
    if (
        not path
        or len(path) > 512
        or path.startswith("/")
        or "\\" in path
        or "//" in path
    ):
        return False
    parts = path.split("/")
    return all(
        part not in {"", ".", ".."}
        and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", part)
        for part in parts
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result
