"""Turn one Telegram message into one grounded, localized reply."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from ..agent import AgentError, AgentPlanInvalid, ProviderUnavailable, SourceGroundedAgent
from ..contracts import ExecutionContext
from ..storage import TenantSecurityStore
from .catalog import BotCatalog
from .i18n import detect_language, message, render_answer

log = logging.getLogger("osai.bot")

READ_CAPABILITY = "drive.table.read"

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
        for scope in catalog.scopes_for(user.role):
            ignore_duplicate(store.grant, catalog.tenant_id, user.actor_id, READ_CAPABILITY, scope)


class BotService:
    def __init__(self, *, agent: SourceGroundedAgent, catalog: BotCatalog) -> None:
        self.agent = agent
        self.catalog = catalog

    def handle_text(self, *, telegram_user_id: int, text: str) -> str:
        lang = detect_language(text)
        user = self.catalog.user(telegram_user_id)
        if user is None:
            return message(lang, "unauthorized", user_id=telegram_user_id)
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
