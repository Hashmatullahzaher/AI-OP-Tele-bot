"""Run the v1 Telegram data Q&A bot.

Usage::

    python -m osai.bot check   # validate configuration, no network calls
    python -m osai.bot run     # start Telegram long polling
    python -m osai.bot run --env-file bot.env   # read settings from a file (local PCs)
    python -m osai.bot web --env-file bot.env   # local browser chat at http://127.0.0.1:8770

Configuration comes from environment variables (see ``deploy/bot/bot.env.example``);
secrets never go in Git.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import webbrowser
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ..agent import SourceGroundedAgent
from ..connectors.google_drive import GoogleDriveConnector, drive_table_manifest
from ..connectors.local_files import LocalFileConnector, local_table_manifest
from ..contracts import CapabilityRegistry, ToolExecutor
from ..llm import LLMSettings, build_planner, settings_from_env
from ..storage import TenantSecurityStore
from ..voice import SpeechToTextProvider, VoiceService
from .catalog import BotCatalog, load_catalog
from .google_auth import ServiceAccountTokenProvider
from .service import BotService, bootstrap_store
from .stt import stt_from_env
from .telegram_poll import TelegramPoller
from .web import build_web_server


@dataclass(frozen=True)
class BotConfig:
    telegram_token: str | None
    catalog: BotCatalog
    google_key_file: str | None
    data_dir: Path
    llm: LLMSettings
    stt: SpeechToTextProvider | None
    web_port: int = 8770


def _default_data_dir(env: Mapping[str, str]) -> str:
    if os.name == "nt" and env.get("LOCALAPPDATA"):
        return str(Path(env["LOCALAPPDATA"]) / "OSAI" / "bot")
    return "/var/lib/osai"


def config_from_env(env: Mapping[str, str], *, need_telegram: bool = True) -> BotConfig:
    token = env.get("OSAI_TELEGRAM_BOT_TOKEN", "").strip() or None
    if need_telegram and (token is None or not re.fullmatch(r"[0-9]{1,20}:[A-Za-z0-9_-]{20,200}", token)):
        raise ValueError("OSAI_TELEGRAM_BOT_TOKEN is missing or malformed")
    catalog = load_catalog(env.get("OSAI_CATALOG_PATH", "/etc/osai/catalog.json"))
    key_file: str | None = None
    if catalog.resources:  # Google Drive sources need the service-account key
        key_file = env.get("OSAI_GOOGLE_KEY_FILE", "/etc/osai/google-service-account.json")
        if not Path(key_file).is_file():
            raise ValueError("OSAI_GOOGLE_KEY_FILE does not point to a file")
    try:
        web_port = int(env.get("OSAI_WEB_PORT", "8770"))
    except ValueError as exc:
        raise ValueError("OSAI_WEB_PORT must be a number") from exc
    data_dir = Path(env.get("OSAI_DATA_DIR") or _default_data_dir(env))
    return BotConfig(
        telegram_token=token,
        catalog=catalog,
        google_key_file=key_file,
        data_dir=data_dir,
        llm=settings_from_env(env),
        stt=stt_from_env(env, data_dir=str(data_dir)),
        web_port=web_port,
    )


def build_service(config: BotConfig) -> BotService:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    store = TenantSecurityStore(config.data_dir / "osai.sqlite3")
    bootstrap_store(store, config.catalog)
    registry = CapabilityRegistry()
    if config.catalog.resources:
        registry.register(
            drive_table_manifest(),
            GoogleDriveConnector(
                connector_id="drive-main",
                resources={resource.alias: resource for resource in config.catalog.resources},
                token_provider=ServiceAccountTokenProvider(cast(str, config.google_key_file)),
            ),
        )
    if config.catalog.local_resources:
        registry.register(
            local_table_manifest(),
            LocalFileConnector(resources={resource.alias: resource for resource in config.catalog.local_resources}),
        )
    agent = SourceGroundedAgent(
        provider=build_planner(config.llm, config.catalog.tables),
        registry=registry,
        executor=ToolExecutor(registry),
        policy_check=store.authorize,
        audit_sink=store,
    )
    voice = VoiceService(store=store, stt_provider=config.stt) if config.stt is not None else None
    return BotService(agent=agent, catalog=config.catalog, voice=voice)


def run_web(config: BotConfig, service: BotService, *, open_browser: bool = True) -> None:  # pragma: no cover
    user = config.catalog.web_user()
    if user is None:
        raise ValueError("catalog.users must contain at least one user for the web chat")
    server = build_web_server(
        answer=lambda text: service.answer_as(user, text),
        user_label=f"{user.actor_id} ({user.role})",
        port=config.web_port,
    )
    url = f"http://127.0.0.1:{config.web_port}/"
    print(f"Local chat running at {url}  (press Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def read_env_file(path: str) -> dict[str, str]:
    """Parse KEY=VALUE lines; blank lines and # comments are ignored."""

    values: dict[str, str] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read env file {path}") from exc
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
            raise ValueError(f"env file line {number} is not KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    env_file: str | None = None
    if "--env-file" in args:
        index = args.index("--env-file")
        if index + 1 >= len(args):
            print("usage: --env-file PATH", file=sys.stderr)
            return 2
        env_file = args[index + 1]
        del args[index : index + 2]
    command = args[0] if args else "run"
    if command not in {"check", "run", "web"}:
        print("usage: python -m osai.bot [check|run|web] [--env-file PATH]", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        env = dict(os.environ)
        if env_file is not None:
            # Real environment variables win over the file.
            env = {**read_env_file(env_file), **env}
        config = config_from_env(env, need_telegram=command == "run")
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if command == "check":
        print(
            f"ok: tenant={config.catalog.tenant_id} tables={len(config.catalog.tables)} "
            f"users={len(config.catalog.users)} llm={config.llm.provider_label}:{config.llm.model} "
            f"voice={'off' if config.stt is None else type(config.stt).__name__} "
            f"telegram={'set' if config.telegram_token else 'not set'}"
        )
        return 0
    service = build_service(config)
    if command == "web":
        try:
            run_web(config, service)
        except (ValueError, OSError) as exc:
            print(f"web chat error: {exc}", file=sys.stderr)
            return 2
        return 0
    poller = TelegramPoller(
        token=cast(str, config.telegram_token),
        on_text=lambda user_id, text: service.handle_text(telegram_user_id=user_id, text=text),
        on_voice=lambda user_id, voice, download: service.handle_voice(
            telegram_user_id=user_id, voice=voice, download=download
        ),
    )
    poller.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
