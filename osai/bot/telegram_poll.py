"""Telegram long polling: no public HTTPS endpoint or webhook is needed."""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from collections.abc import Callable, Mapping
from typing import Any

from ..channels.telegram import TelegramError, TelegramTransport, UrllibTelegramTransport
from ..channels.telegram_voice import UrllibTelegramVoiceTransport, _safe_file_path
from .i18n import message

log = logging.getLogger("osai.bot.telegram")

MAX_VOICE_BYTES = 10_000_000

TextHandler = Callable[[int, str], str]
VoiceHandler = Callable[[int, Mapping[str, Any], Callable[[str], bytes]], str]


class TelegramPoller:
    def __init__(
        self,
        *,
        token: str,
        on_text: TextHandler,
        on_voice: VoiceHandler | None = None,
        transport: TelegramTransport | None = None,
        media: Any = None,
        poll_timeout: int = 50,
    ) -> None:
        if not re.fullmatch(r"[0-9]{1,20}:[A-Za-z0-9_-]{20,200}", token):
            raise ValueError("Telegram bot token is missing or malformed")
        self._base = f"https://api.telegram.org/bot{urllib.parse.quote(token, safe=':')}"
        self.on_text = on_text
        self.on_voice = on_voice
        self.transport = transport or UrllibTelegramTransport(max_response_bytes=5_000_000)
        self.media = media or UrllibTelegramVoiceTransport()
        self.poll_timeout = poll_timeout
        self.offset = 0

    def call(self, method: str, payload: Mapping[str, Any], timeout: int = 30) -> Any:
        response = self.transport.post_json(f"{self._base}/{method}", payload=payload, timeout_seconds=timeout)
        if response.get("ok") is not True:
            raise TelegramError(f"telegram {method} failed")
        return response.get("result")

    def send(self, chat_id: int, text: str) -> None:
        self.call("sendMessage", {"chat_id": chat_id, "text": text[:4_096], "disable_web_page_preview": True})

    def download_file(self, file_id: str) -> bytes:
        """Resolve and download one Telegram file (voice notes are small)."""

        result = self.call("getFile", {"file_id": file_id})
        path = result.get("file_path") if isinstance(result, Mapping) else None
        if not isinstance(path, str) or not _safe_file_path(path):
            raise TelegramError("telegram file path unavailable")
        encoded = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
        url = self._base.replace("/bot", "/file/bot", 1) + "/" + encoded
        return self.media.get_bytes(url, timeout_seconds=30, max_bytes=MAX_VOICE_BYTES)

    def poll_once(self) -> int:
        updates = self.call(
            "getUpdates",
            {"offset": self.offset, "timeout": self.poll_timeout, "allowed_updates": ["message"]},
            timeout=self.poll_timeout + 10,
        )
        if not isinstance(updates, list):
            return 0
        for update in updates:
            update_id = update.get("update_id") if isinstance(update, Mapping) else None
            if not isinstance(update_id, int):
                continue
            self.offset = max(self.offset, update_id + 1)
            try:
                self._dispatch(update)
            except Exception:
                log.exception("failed to handle update %s", update_id)
        return len(updates)

    def _dispatch(self, update: Mapping[str, Any]) -> None:
        msg = update.get("message")
        if not isinstance(msg, Mapping):
            return
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        if chat.get("type") != "private" or sender.get("is_bot"):
            return  # v1 answers private chats only
        chat_id, user_id = chat.get("id"), sender.get("id")
        if not isinstance(chat_id, int) or not isinstance(user_id, int):
            return
        text = msg.get("text")
        voice = msg.get("voice")
        if isinstance(text, str) and text.strip():
            self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
            reply = self.on_text(user_id, text)
        elif isinstance(voice, Mapping):
            if self.on_voice is None:
                reply = message("fa", "voice_unsupported") + "\n" + message("en", "voice_unsupported")
            else:
                self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
                reply = self.on_voice(user_id, voice, self.download_file)
        else:
            reply = message("fa", "text_only") + "\n" + message("en", "text_only")
        self.send(chat_id, reply)

    def run_forever(self) -> None:  # pragma: no cover - network loop
        backoff = 1
        log.info("telegram polling started")
        while True:
            try:
                self.poll_once()
                backoff = 1
            except TelegramError as exc:
                log.warning("telegram polling error: %s; retrying in %ss", exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
