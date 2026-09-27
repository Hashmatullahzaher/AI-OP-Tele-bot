"""Secure Telegram private-text channel foundation for OS AI Core F2.

Security boundaries:
- Telegram webhook secret is resolved at runtime and compared in constant time.
- only private text messages are accepted in F2;
- update_id replay is persisted and denied;
- Telegram chats must be paired out-of-band to an active tenant actor;
- pairing codes are high-entropy, short-lived, single-use and stored only as hashes;
- chat text is never written to the security/audit log by this module;
- bot tokens are injected at send time and never persisted here.

Live Telegram credentials and internet exposure remain deployment concerns.
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
from datetime import UTC, datetime
from typing import Any, NoReturn, Protocol

from ..contracts import ExecutionContext, PolicyDenied
from ..storage import TenantSecurityStore


class TelegramError(RuntimeError):
    """Base class for sanitized Telegram failures."""


class TelegramAccessDenied(TelegramError):
    """Webhook, pairing, identity, or chat policy denied the request."""


class TelegramUnavailable(TelegramError):
    """Telegram API is unavailable or returned a transient failure."""


class WebhookSecretProvider(Protocol):
    def webhook_secret(self, *, bot_alias: str) -> str:
        """Return the expected Telegram webhook secret from a secret boundary."""


class BotTokenProvider(Protocol):
    def bot_token(self, *, bot_alias: str) -> str:
        """Return the bot token from a secret boundary."""


class SecurityEventSink(Protocol):
    def emit(self, *, event: str, metadata: Mapping[str, str | int]) -> None:
        """Record a redacted channel-security event."""


class TelegramTransport(Protocol):
    def post_json(
        self,
        url: str,
        *,
        payload: Mapping[str, Any],
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        """POST one bounded Telegram Bot API JSON request."""


@dataclass(frozen=True)
class StaticWebhookSecretProvider:
    """Test/development provider. Never commit a real secret."""

    secret: str

    def webhook_secret(self, *, bot_alias: str) -> str:
        return self.secret


@dataclass(frozen=True)
class StaticBotTokenProvider:
    """Test/development provider. Never commit a real bot token."""

    token: str

    def bot_token(self, *, bot_alias: str) -> str:
        return self.token


@dataclass(frozen=True)
class TelegramIngressResult:
    kind: str
    context: ExecutionContext
    chat_id: int
    telegram_user_id: int
    message_id: int
    update_id: int
    text: str | None = None


class UrllibTelegramTransport:
    """Telegram-only HTTPS transport with redirects disabled."""

    _HOST = "api.telegram.org"

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise TelegramAccessDenied("telegram redirect refused")

    def __init__(self, *, max_response_bytes: int = 1_000_000) -> None:
        if max_response_bytes < 1 or max_response_bytes > 5_000_000:
            raise ValueError("max_response_bytes out of range")
        self.max_response_bytes = max_response_bytes
        self._opener = urllib.request.build_opener(self._NoRedirect())

    def post_json(
        self,
        url: str,
        *,
        payload: Mapping[str, Any],
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != self._HOST or parsed.username or parsed.password:
            raise TelegramAccessDenied("telegram host is not allowed")
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise TelegramAccessDenied("telegram authorization denied") from exc
            if exc.code == 429 or 500 <= exc.code <= 599:
                raise TelegramUnavailable("telegram API unavailable") from exc
            raise TelegramUnavailable("telegram API request failed") from exc
        except urllib.error.URLError as exc:
            raise TelegramUnavailable("telegram API unavailable") from exc
        if len(raw) > self.max_response_bytes:
            raise TelegramUnavailable("telegram response exceeded configured limit")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TelegramUnavailable("telegram returned invalid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise TelegramUnavailable("telegram returned an unexpected response")
        return decoded


class TelegramIngress:
    """Validate, de-duplicate, pair, and route one Telegram webhook update."""

    _SECRET_HEADER = "x-telegram-bot-api-secret-token"
    _PAIR_RE = re.compile(r"^/pair(?:@[A-Za-z0-9_]{3,64})?\s+([A-Za-z0-9_-]{20,256})$")

    def __init__(
        self,
        *,
        store: TenantSecurityStore,
        secret_provider: WebhookSecretProvider,
        security_sink: SecurityEventSink,
        max_body_bytes: int = 256_000,
        max_text_chars: int = 4_096,
    ) -> None:
        if max_body_bytes < 1 or max_body_bytes > 2_000_000:
            raise ValueError("max_body_bytes out of range")
        if max_text_chars < 1 or max_text_chars > 4_096:
            raise ValueError("max_text_chars out of range")
        self.store = store
        self.secret_provider = secret_provider
        self.security_sink = security_sink
        self.max_body_bytes = max_body_bytes
        self.max_text_chars = max_text_chars

    def handle(
        self,
        *,
        bot_alias: str,
        headers: Mapping[str, str],
        body: bytes,
        now_epoch: int | None = None,
    ) -> TelegramIngressResult:
        now = _now_epoch() if now_epoch is None else now_epoch
        if now < 0:
            raise TelegramAccessDenied("telegram request denied")
        _validate_bot_alias(bot_alias)
        self._verify_webhook_secret(bot_alias, headers)
        update = self._parse_update(body)
        update_id = _required_int(update, "update_id", minimum=0)
        try:
            self.store.claim_telegram_update(
                bot_alias=bot_alias,
                update_id=update_id,
                received_at=now,
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
        text = message.get("text")
        if not isinstance(text, str):
            self._deny("telegram.non_text_denied", bot_alias, update_id)
        clean_text = text.strip()
        if not clean_text or len(clean_text) > self.max_text_chars:
            self._deny("telegram.invalid_text", bot_alias, update_id)

        pairing = self._PAIR_RE.fullmatch(clean_text)
        if pairing is not None:
            try:
                binding = self.store.consume_telegram_pairing_challenge(
                    bot_alias=bot_alias,
                    code=pairing.group(1),
                    telegram_user_id=telegram_user_id,
                    chat_id=chat_id,
                    now_epoch=now,
                )
            except PolicyDenied as exc:
                self.security_sink.emit(
                    event="telegram.pairing_denied",
                    metadata={"bot_alias": bot_alias, "update_id": update_id},
                )
                raise TelegramAccessDenied("telegram pairing denied") from exc
            context = ExecutionContext.issue(
                tenant_id=binding.tenant_id,
                actor_id=binding.actor_id,
                request_classification="internal",
                session_assurance="telegram-paired",
            )
            self.store.append_audit(
                context=context,
                event="telegram.paired",
                result="OK",
                resource_scope=f"telegram:{bot_alias}",
                sensitive_payload={"update_id": update_id},
            )
            self.security_sink.emit(
                event="telegram.paired",
                metadata={"bot_alias": bot_alias, "update_id": update_id},
            )
            return TelegramIngressResult(
                kind="paired",
                context=context,
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                message_id=message_id,
                update_id=update_id,
            )

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
        context = ExecutionContext.issue(
            tenant_id=binding.tenant_id,
            actor_id=binding.actor_id,
            request_classification="internal",
            session_assurance="telegram-paired",
        )
        self.store.append_audit(
            context=context,
            event="telegram.message.accepted",
            result="OK",
            resource_scope=f"telegram:{bot_alias}",
            sensitive_payload={"update_id": update_id, "message_id": message_id},
        )
        self.security_sink.emit(
            event="telegram.message.accepted",
            metadata={"bot_alias": bot_alias, "update_id": update_id},
        )
        return TelegramIngressResult(
            kind="message",
            context=context,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            message_id=message_id,
            update_id=update_id,
            text=clean_text,
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


class TelegramSender:
    """Send private text through the Bot API using a runtime token provider."""

    def __init__(
        self,
        *,
        store: TenantSecurityStore,
        token_provider: BotTokenProvider,
        transport: TelegramTransport | None = None,
        timeout_seconds: int = 20,
    ) -> None:
        if timeout_seconds < 1 or timeout_seconds > 60:
            raise ValueError("timeout_seconds out of range")
        self.store = store
        self.token_provider = token_provider
        self.transport = transport or UrllibTelegramTransport()
        self.timeout_seconds = timeout_seconds

    def send_text(
        self,
        *,
        context: ExecutionContext,
        bot_alias: str,
        chat_id: int,
        telegram_user_id: int,
        text: str,
    ) -> int:
        _validate_bot_alias(bot_alias)
        clean = text.strip()
        if chat_id <= 0 or telegram_user_id <= 0 or not clean or len(clean) > 4_096:
            raise TelegramAccessDenied("telegram outbound message denied")
        try:
            binding = self.store.resolve_telegram_binding(
                bot_alias=bot_alias,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
            )
        except PolicyDenied as exc:
            raise TelegramAccessDenied("telegram outbound identity unavailable") from exc
        if binding.tenant_id != context.tenant_id or binding.actor_id != context.actor_id:
            raise TelegramAccessDenied("telegram outbound identity unavailable")
        token = self.token_provider.bot_token(bot_alias=bot_alias)
        if not re.fullmatch(r"[0-9]{1,20}:[A-Za-z0-9_-]{20,200}", token):
            raise TelegramAccessDenied("telegram bot credential unavailable")
        encoded_token = urllib.parse.quote(token, safe=":")
        url = f"https://api.telegram.org/bot{encoded_token}/sendMessage"
        response = self.transport.post_json(
            url,
            payload={
                "chat_id": chat_id,
                "text": clean,
                "disable_web_page_preview": True,
            },
            timeout_seconds=self.timeout_seconds,
        )
        if response.get("ok") is not True:
            raise TelegramUnavailable("telegram send failed")
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise TelegramUnavailable("telegram send returned invalid result")
        message_id = result.get("message_id")
        if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
            raise TelegramUnavailable("telegram send returned invalid message id")
        return message_id


def _validate_bot_alias(bot_alias: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", bot_alias):
        raise TelegramAccessDenied("telegram bot unavailable")


def _required_int(source: Mapping[str, Any], key: str, *, minimum: int) -> int:
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TelegramAccessDenied("telegram request denied")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _now_epoch() -> int:
    return int(datetime.now(UTC).timestamp())
