"""Run the v1 Telegram data Q&A bot.

Usage::

    python -m osai.bot check   # validate configuration, no network calls
    python -m osai.bot run     # start Telegram long polling

Configuration comes from environment variables (see ``deploy/bot/bot.env.example``);
secrets never go in Git.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..agent import SourceGroundedAgent
from ..connectors.google_drive import GoogleDriveConnector, drive_table_manifest
from ..contracts import CapabilityRegistry, ToolExecutor
from ..llm import LLMSettings, build_planner, settings_from_env
from ..storage import TenantSecurityStore
from .catalog import BotCatalog, load_catalog
from .google_auth import ServiceAccountTokenProvider
from .service import BotService, bootstrap_store
from .telegram_poll import TelegramPoller


@dataclass(frozen=True)
class BotConfig:
    telegram_token: str
    catalog: BotCatalog
    google_key_file: str
    data_dir: Path
    llm: LLMSettings


def config_from_env(env: Mapping[str, str]) -> BotConfig:
    token = env.get("OSAI_TELEGRAM_BOT_TOKEN", "").strip()
    if not re.fullmatch(r"[0-9]{1,20}:[A-Za-z0-9_-]{20,200}", token):
        raise ValueError("OSAI_TELEGRAM_BOT_TOKEN is missing or malformed")
    key_file = env.get("OSAI_GOOGLE_KEY_FILE", "/etc/osai/google-service-account.json")
    if not Path(key_file).is_file():
        raise ValueError("OSAI_GOOGLE_KEY_FILE does not point to a file")
    return BotConfig(
        telegram_token=token,
        catalog=load_catalog(env.get("OSAI_CATALOG_PATH", "/etc/osai/catalog.json")),
        google_key_file=key_file,
        data_dir=Path(env.get("OSAI_DATA_DIR", "/var/lib/osai")),
        llm=settings_from_env(env),
    )


def build_service(config: BotConfig) -> BotService:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    store = TenantSecurityStore(config.data_dir / "osai.sqlite3")
    bootstrap_store(store, config.catalog)
    connector = GoogleDriveConnector(
        connector_id="drive-main",
        resources={resource.alias: resource for resource in config.catalog.resources},
        token_provider=ServiceAccountTokenProvider(config.google_key_file),
    )
    registry = CapabilityRegistry()
    registry.register(drive_table_manifest(), connector)
    agent = SourceGroundedAgent(
        provider=build_planner(config.llm, config.catalog.tables),
        registry=registry,
        executor=ToolExecutor(registry),
        policy_check=store.authorize,
        audit_sink=store,
    )
    return BotService(agent=agent, catalog=config.catalog)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else "run"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = config_from_env(os.environ)
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if command == "check":
        print(
            f"ok: tenant={config.catalog.tenant_id} tables={len(config.catalog.tables)} "
            f"users={len(config.catalog.users)} llm={config.llm.provider_label}:{config.llm.model}"
        )
        return 0
    if command != "run":
        print("usage: python -m osai.bot [check|run]", file=sys.stderr)
        return 2
    service = build_service(config)
    poller = TelegramPoller(
        token=config.telegram_token,
        on_text=lambda user_id, text: service.handle_text(telegram_user_id=user_id, text=text),
    )
    poller.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
