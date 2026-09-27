"""Turn one Telegram message into one grounded, localized reply."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any

from ..agent import AgentError, AgentPlanInvalid, ProviderUnavailable, SourceGroundedAgent
from ..connectors.local_files import LOCAL_CAPABILITY
from ..contracts import ExecutionContext
from ..storage import TenantSecurityStore
from ..voice import VoiceError, VoiceInputDenied, VoiceService
from .catalog import BotCatalog, BotUser
from .i18n import detect_language, message, render_answer

log = logging.getLogger("osai.bot")

READ_CAPABILITY = "drive.table.read"
READ_CAPABILITIES = (READ_CAPABILITY, LOCAL_CAPABILITY)

_ERROR_KEYS = {
    "SOURCE_ACCESS_DENIED": "denied",
    "CAPABILITY_NOT_ALLOWED": "denied",
    "SOURCE_UNAVAILABLE": "unavailable",
    "SOURCE_SCHEMA_AMBIGUOUS": "ambiguous",
    "REQUEST_INVALID": "not_understood",
}


def bootstrap_store(store: TenantSecurityStore, catalog: BotCatalog) -> None:
    """Idempotently create the tenant, actors and read grants from the catalog."""

    def ignore_duplicate(action: Callable[..., None], *args: str, **kwargs: str) -> None:
        try:
            action(*args, **kwargs)
        except sqlite3.IntegrityError:
            pass

    ignore_duplicate(store.add_tenant, catalog.tenant_id)
    for user in catalog.users:
        ignore_duplicate(store.add_actor, catalog.tenant_id, user.actor_id, role="TENANT_ADMIN")
        # Each connector re-checks its own scope prefix (drive:/local:), so both
        # read capabilities are granted the role's full scope list.
        for capability in READ_CAPABILITIES:
            for scope in catalog.scopes_for(user.role):
                ignore_duplicate(store.grant, catalog.tenant_id, user.actor_id, capability, scope)


Downloader = Callable[[str], bytes]

MAX_VOICE_SECONDS = 120
_WHISPER_TO_REPLY_LANG = {"fa": "fa", "ps": "ps"}


class BotService:
    def __init__(
        self,
        *,
        agent: SourceGroundedAgent,
        catalog: BotCatalog,
        voice: VoiceService | None = None,
    ) -> None:
        self.agent = agent
        self.catalog = catalog
        self.voice = voice

    def handle_voice(self, *, telegram_user_id: int, voice: Mapping[str, Any], download: Downloader) -> str:
        """Authorize first, then download, transcribe, answer and echo the transcript."""

        user = self.catalog.user(telegram_user_id)
        if user is None:
            return (
                message("fa", "unauthorized", user_id=telegram_user_id)
                + "\n"
                + message("en", "unauthorized", user_id=telegram_user_id)
            )
        if self.voice is None:
            return message("fa", "voice_unsupported") + "\n" + message("en", "voice_unsupported")
        file_id = voice.get("file_id")
        duration = voice.get("duration")
        if not isinstance(file_id, str) or isinstance(duration, bool) or not isinstance(duration, int):
            return message("fa", "voice_failed") + "\n" + message("en", "voice_failed")
        if duration > MAX_VOICE_SECONDS:
            return (
                message("fa", "voice_too_long", seconds=MAX_VOICE_SECONDS)
                + "\n"
                + message("en", "voice_too_long", seconds=MAX_VOICE_SECONDS)
            )
        context = ExecutionContext.issue(tenant_id=self.catalog.tenant_id, actor_id=user.actor_id)
        try:
            audio = download(file_id)
            result = self.voice.transcribe(
                context=context,
                audio=audio,
                mime_type=str(voice.get("mime_type") or "audio/ogg"),
                duration_seconds=max(duration, 1),
                language_hint=None,
            )
        except VoiceInputDenied:
            return message("fa", "voice_failed") + "\n" + message("en", "voice_failed")
        except VoiceError as exc:
            log.warning("voice unavailable correlation=%s reason=%s", context.correlation_id, exc)
            return message("fa", "busy") + "\n" + message("en", "busy")
        except Exception:
            log.exception("voice download/transcription failed correlation=%s", context.correlation_id)
            return message("fa", "voice_failed") + "\n" + message("en", "voice_failed")
        lang = _WHISPER_TO_REPLY_LANG.get(result.language) or detect_language(result.text)
        answer = self.handle_text(telegram_user_id=telegram_user_id, text=result.text)
        return message(lang, "heard", text=result.text) + "\n\n" + answer

    def handle_text(self, *, telegram_user_id: int, text: str) -> str:
        user = self.catalog.user(telegram_user_id)
        if user is None:
            return message(detect_language(text), "unauthorized", user_id=telegram_user_id)
        return self.answer_as(user, text)

    def answer_as(self, user: BotUser, text: str) -> str:
        """Answer for an already-identified catalog user (Telegram or local web chat)."""

        lang = detect_language(text)
        clean = text.strip()
        command = clean.split(maxsplit=1)[0].split("@")[0] if clean else ""
        if command in {"/start", "/help"}:
            return "\n\n".join(message(code, "welcome") for code in ("fa", "ps", "en"))
        context = ExecutionContext.issue(
            tenant_id=self.catalog.tenant_id,
            actor_id=user.actor_id,
            resource_scope=self.catalog.scopes_for(user.role),
        )
        try:
            answer = self.agent.ask(message=clean, context=context)
        except ProviderUnavailable as exc:
            log.warning("provider unavailable correlation=%s reason=%s", context.correlation_id, exc)
            return message(lang, "busy")
        except AgentPlanInvalid as exc:
            log.info("plan rejected correlation=%s reason=%s", context.correlation_id, exc)
            return message(lang, "not_understood")
        except AgentError as exc:
            log.info("agent error correlation=%s code=%s", context.correlation_id, exc.code)
            return message(lang, _ERROR_KEYS.get(exc.code, "error"))
        except Exception:
            log.exception("unexpected failure correlation=%s", context.correlation_id)
            return message(lang, "error")
        sources = [
            {
                "source_id": source.source_id,
                "sheet": source.locator.get("sheet_name") or source.locator.get("table_alias"),
                "revision": source.revision,
            }
            for source in answer.sources
        ]
        log.info(
            "answered correlation=%s capability=%s provider=%s",
            answer.correlation_id,
            answer.capability,
            answer.provider_name,
        )
        return render_answer(lang, answer.authoritative_data, sources)
