"""Executable Telegram -> planner -> approval -> F9 -> Excel UAT.

The planning provider in this harness is deterministic and synthetic. It uses the
same ModelProvider protocol that a live LLM provider will implement, so this
tests the trusted orchestration path without requiring a production API key.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .action_approval import PendingApprovalStore
from .actions import ActionCoordinator, ActionJournal, default_action_definitions
from .agent import ToolCatalogEntry
from .channels.telegram import StaticWebhookSecretProvider, TelegramIngress
from .channels.telegram_actions import TelegramActionBridge
from .connectors.local_excel import ExcelSandboxWriteAdapter, initialize_excel_sandbox
from .storage import TenantSecurityStore
from .write_agent import PermissionedWriteAgent

BOT_ALIAS = "excel-bot"
WEBHOOK_SECRET = "uat_webhook_secret_123456"
SOURCE = "excel-pilot"
TENANT = "excel-uat"
ACTOR = "owner"
CHAT_ID = 42001
USER_ID = 42001


class SilentSecuritySink:
    def emit(self, *, event: str, metadata: Mapping[str, str | int]) -> None:
        return None


class DeterministicUATPlanningProvider:
    """Synthetic planner for plumbing/UAT only; not a production AI model."""

    provider_name = "deterministic-uat-planner"

    _PLANS: dict[str, Mapping[str, Any]] = {
        "Create Ahmad Trading as a company customer": {
            "schema_version": "0.1",
            "action": "write",
            "capability": "customer.create",
            "arguments": {
                "name": "Ahmad Trading",
                "customer_type": "COMPANY",
                "phone": "0700000000",
                "address": "Mazar-e-Sharif",
            },
        },
        "Update Ahmad Trading phone to 0799999999": {
            "schema_version": "0.1",
            "action": "write",
            "capability": "customer.update",
            "arguments": {
                "customer_id": "CUS-000001",
                "changes": {"phone": "0799999999"},
            },
        },
        "Create the office supplies procurement request": {
            "schema_version": "0.1",
            "action": "write",
            "capability": "procurement.request.create",
            "arguments": {
                "requester": "Hash",
                "department": "Admin",
                "currency": "AFN",
                "justification": "Telegram AI Excel UAT",
                "items": [
                    {
                        "description": "Paper",
                        "quantity": "2",
                        "unit_estimated_cost": "1250.50",
                    },
                    {
                        "description": "Toner",
                        "quantity": "1",
                        "unit_estimated_cost": "5000",
                    },
                ],
            },
        },
        "Create a 9000 AFN office expense draft voucher": {
            "schema_version": "0.1",
            "action": "write",
            "capability": "finance.draft_voucher.create",
            "arguments": {
                "voucher_date": "2026-09-26",
                "currency": "AFN",
                "description": "Office expense",
                "entries": [
                    {
                        "account_code": "6300",
                        "debit": "9000",
                        "credit": "0",
                        "memo": "Office expense",
                    },
                    {
                        "account_code": "1100",
                        "debit": "0",
                        "credit": "9000",
                        "memo": "Cash",
                    },
                ],
            },
        },
    }

    def plan(
        self,
        *,
        user_message: str,
        tool_catalog: Sequence[ToolCatalogEntry],
        correlation_id: str,
    ) -> Mapping[str, Any]:
        allowed = {entry.capability for entry in tool_catalog}
        plan = self._PLANS.get(user_message)
        if plan is None:
            raise ValueError("UAT planner has no scripted plan for this message")
        capability = str(plan["capability"])
        if capability not in allowed:
            raise ValueError("scripted UAT plan is outside server catalog")
        return dict(plan)


def _telegram_body(update_id: int, text: str) -> bytes:
    return json.dumps(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "from": {"id": USER_ID, "is_bot": False, "first_name": "Excel UAT"},
                "chat": {"id": CHAT_ID, "type": "private"},
                "text": text,
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _approval_ref(response: str) -> str:
    match = re.search(r"/approve (tg:[A-Za-z0-9_-]+)", response)
    if match is None:
        raise RuntimeError("UAT proposal did not return an approval token")
    return match.group(1)


def run(workbook_path: Path, database_path: Path) -> dict[str, Any]:
    initialize_excel_sandbox(workbook_path)
    if database_path.exists():
        database_path.unlink()

    store = TenantSecurityStore(database_path)
    approvals = PendingApprovalStore(database_path)
    journal = ActionJournal(database_path)
    try:
        store.add_tenant(TENANT)
        store.add_actor(TENANT, ACTOR, role="TENANT_ADMIN")
        for capability in (
            "customer.create",
            "customer.update",
            "procurement.request.create",
            "finance.draft_voucher.create",
        ):
            store.grant(TENANT, ACTOR, capability, f"client_api:{SOURCE}")

        coordinator = ActionCoordinator(
            security_store=store,
            journal=journal,
            definitions=default_action_definitions(SOURCE),
            adapter=ExcelSandboxWriteAdapter(
                workbook_path=workbook_path,
                source_alias=SOURCE,
            ),
            approval_verifier=approvals,
        )
        write_agent = PermissionedWriteAgent(
            provider=DeterministicUATPlanningProvider(),
            coordinator=coordinator,
            approval_store=approvals,
            audit_sink=store,
            source_scope=f"client_api:{SOURCE}",
        )
        bridge = TelegramActionBridge(write_agent=write_agent)
        ingress = TelegramIngress(
            store=store,
            secret_provider=StaticWebhookSecretProvider(WEBHOOK_SECRET),
            security_sink=SilentSecuritySink(),
        )

        now = int(time.time())
        pair_code = store.create_telegram_pairing_challenge(
            tenant_id=TENANT,
            actor_id=ACTOR,
            bot_alias=BOT_ALIAS,
            now_epoch=now,
        )
        pair_result = ingress.handle(
            bot_alias=BOT_ALIAS,
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
            body=_telegram_body(1, f"/pair {pair_code}"),
            now_epoch=now + 1,
        )
        bridge.handle(pair_result)

        transcript: list[dict[str, str]] = []
        update_id = 2
        messages = (
            "Create Ahmad Trading as a company customer",
            "Update Ahmad Trading phone to 0799999999",
            "Create the office supplies procurement request",
            "Create a 9000 AFN office expense draft voucher",
        )
        for user_message in messages:
            proposal_ingress = ingress.handle(
                bot_alias=BOT_ALIAS,
                headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
                body=_telegram_body(update_id, user_message),
                now_epoch=now + update_id,
            )
            proposal = bridge.handle(proposal_ingress)
            approval_ref = _approval_ref(proposal)
            transcript.append({"user": user_message, "system": proposal})

            update_id += 1
            approval_message = f"/approve {approval_ref}"
            approval_ingress = ingress.handle(
                bot_alias=BOT_ALIAS,
                headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
                body=_telegram_body(update_id, approval_message),
                now_epoch=now + update_id,
            )
            completed = bridge.handle(approval_ingress)
            transcript.append({"user": approval_message, "system": completed})
            if "CHANGE COMPLETED" not in completed:
                raise RuntimeError("Telegram Excel UAT mutation did not complete")
            update_id += 1

        audit_records = store.audit_records(TENANT)
        return {
            "ok": True,
            "workbook": str(workbook_path),
            "database": str(database_path),
            "planner": "deterministic-uat-planner",
            "telegram_updates": update_id - 1,
            "completed_actions": 4,
            "audit_events": len(audit_records),
            "audit_chain_ok": store.verify_audit_chain(TENANT),
            "transcript": transcript,
        }
    finally:
        journal.close()
        approvals.close()
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram AI Excel UAT")
    parser.add_argument("--path", required=True, help="absolute or relative XLSX output path")
    parser.add_argument("--db", required=True, help="absolute or relative SQLite UAT path")
    parser.add_argument("--transcript", help="optional JSON transcript output path")
    args = parser.parse_args()

    result = run(Path(args.path).expanduser().resolve(), Path(args.db).expanduser().resolve())
    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output)
    if args.transcript:
        transcript_path = Path(args.transcript).expanduser().resolve()
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
