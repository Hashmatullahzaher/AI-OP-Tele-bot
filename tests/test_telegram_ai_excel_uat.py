import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from osai.action_approval import PendingApprovalStore
from osai.actions import ActionCoordinator, ActionJournal, default_action_definitions
from osai.channels.telegram import StaticWebhookSecretProvider, TelegramIngress
from osai.channels.telegram_actions import TelegramActionBridge
from osai.connectors.google_drive import parse_xlsx_table
from osai.connectors.local_excel import ExcelSandboxWriteAdapter, initialize_excel_sandbox
from osai.storage import TenantSecurityStore
from osai.write_agent import PermissionedWriteAgent

BOT_ALIAS = "excel-bot"
WEBHOOK_SECRET = "excel_webhook_secret_123"
SOURCE = "excel-pilot"


class FakeSecuritySink:
    def __init__(self):
        self.events = []

    def emit(self, *, event, metadata):
        self.events.append((event, dict(metadata)))


class ScriptedProvider:
    provider_name = "scripted-write-provider"

    def __init__(self, plans):
        self.plans = dict(plans)
        self.calls = []

    def plan(self, *, user_message, tool_catalog, correlation_id):
        self.calls.append(
            {
                "message": user_message,
                "catalog": tuple(entry.capability for entry in tool_catalog),
                "correlation_id": correlation_id,
            }
        )
        return dict(self.plans[user_message])


def tg_update(
    update_id,
    *,
    text,
    chat_id=42,
    user_id=42,
    message_id=None,
):
    return json.dumps(
        {
            "update_id": update_id,
            "message": {
                "message_id": message_id or update_id,
                "from": {"id": user_id, "is_bot": False, "first_name": "Pilot"},
                "chat": {"id": chat_id, "type": "private"},
                "text": text,
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def tg_headers():
    return {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET}


class TelegramAIExcelUATTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "osai.sqlite3"
        self.xlsx = root / "Telegram-AI-Excel-UAT.xlsx"
        initialize_excel_sandbox(self.xlsx)

        self.store = TenantSecurityStore(self.db)
        self.store.add_tenant("alpha")
        self.store.add_actor("alpha", "owner", role="TENANT_ADMIN")
        for capability in (
            "customer.create",
            "customer.update",
            "procurement.request.create",
            "finance.draft_voucher.create",
        ):
            self.store.grant("alpha", "owner", capability, f"client_api:{SOURCE}")

        self.approvals = PendingApprovalStore(self.db)
        self.journal = ActionJournal(self.db)
        self.adapter = ExcelSandboxWriteAdapter(
            workbook_path=self.xlsx,
            source_alias=SOURCE,
        )
        self.coordinator = ActionCoordinator(
            security_store=self.store,
            journal=self.journal,
            definitions=default_action_definitions(SOURCE),
            adapter=self.adapter,
            approval_verifier=self.approvals,
        )
        self.provider = ScriptedProvider(
            {
                "Create Ahmad Trading as a company customer": {
                    "schema_version": "0.1",
                    "action": "write",
                    "capability": "customer.create",
                    "arguments": {
                        "name": "Ahmad Trading",
                        "customer_type": "COMPANY",
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
                "Do something unsupported": {
                    "schema_version": "0.1",
                    "action": "write",
                    "capability": "finance.voucher.post",
                    "arguments": {"voucher_id": "DV-1"},
                },
            }
        )
        self.write_agent = PermissionedWriteAgent(
            provider=self.provider,
            coordinator=self.coordinator,
            approval_store=self.approvals,
            audit_sink=self.store,
            source_scope=f"client_api:{SOURCE}",
        )
        self.bridge = TelegramActionBridge(write_agent=self.write_agent)
        self.sink = FakeSecuritySink()
        self.ingress = TelegramIngress(
            store=self.store,
            secret_provider=StaticWebhookSecretProvider(WEBHOOK_SECRET),
            security_sink=self.sink,
        )
        self.now = int(time.time())
        code = self.store.create_telegram_pairing_challenge(
            tenant_id="alpha",
            actor_id="owner",
            bot_alias=BOT_ALIAS,
            now_epoch=self.now,
        )
        paired = self.ingress.handle(
            bot_alias=BOT_ALIAS,
            headers=tg_headers(),
            body=tg_update(1, text=f"/pair {code}"),
            now_epoch=self.now + 1,
        )
        self.assertEqual(paired.kind, "paired")

    def tearDown(self):
        self.journal.close()
        self.approvals.close()
        self.store.close()
        self.tmp.cleanup()

    def message(self, update_id, text, *, chat_id=42, user_id=42):
        ingress = self.ingress.handle(
            bot_alias=BOT_ALIAS,
            headers=tg_headers(),
            body=tg_update(
                update_id,
                text=text,
                chat_id=chat_id,
                user_id=user_id,
            ),
            now_epoch=self.now + update_id,
        )
        return self.bridge.handle(ingress)

    def sheet(self, name):
        return parse_xlsx_table(
            self.xlsx.read_bytes(),
            sheet_name=name,
            max_rows=100,
            max_columns=20,
            max_uncompressed_bytes=2_000_000,
        )

    def approval_ref(self, response):
        match = re.search(r"/approve (tg:[A-Za-z0-9_-]+)", response)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_telegram_ai_proposal_does_not_write_until_separate_approval(self):
        response = self.message(2, "Create Ahmad Trading as a company customer")
        self.assertIn("PROPOSED CHANGE", response)
        self.assertIn("nothing has been written yet", response)
        self.assertEqual(len(self.sheet("Customers")), 1)

        token = self.approval_ref(response)
        completed = self.message(3, f"/approve {token}")
        self.assertIn("CHANGE COMPLETED", completed)
        self.assertIn("CUS-000001", completed)

        rows = self.sheet("Customers")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "CUS-000001")
        self.assertEqual(rows[1][1], "Ahmad Trading")

        replay = self.message(4, f"/approve {token}")
        self.assertIn("invalid, expired, already used", replay)
        self.assertEqual(len(self.sheet("Customers")), 2)

        events = [record.event for record in self.store.audit_records("alpha")]
        self.assertIn("agent.write_proposed", events)
        self.assertIn("action.requested", events)
        self.assertIn("action.completed", events)

    def test_balanced_draft_voucher_goes_through_same_telegram_approval_path(self):
        response = self.message(5, "Create a 9000 AFN office expense draft voucher")
        self.assertIn('"status": "DRAFT"', response)
        token = self.approval_ref(response)
        completed = self.message(6, f"/approve {token}")
        self.assertIn("DV-000001", completed)
        self.assertIn("State: DRAFT", completed)

        vouchers = self.sheet("DraftVouchers")
        entries = self.sheet("VoucherEntries")
        self.assertEqual(vouchers[1][4], "9000.00")
        self.assertEqual(vouchers[1][5], "9000.00")
        self.assertEqual(vouchers[1][6], "DRAFT")
        self.assertEqual(len(entries), 3)

    def test_cancelled_proposal_never_mutates_excel(self):
        response = self.message(7, "Create Ahmad Trading as a company customer")
        token = self.approval_ref(response)
        cancelled = self.message(8, f"/cancel {token}")
        self.assertIn("cancelled", cancelled)
        self.assertEqual(len(self.sheet("Customers")), 1)

        later = self.message(9, f"/approve {token}")
        self.assertIn("invalid, expired, already used", later)
        self.assertEqual(len(self.sheet("Customers")), 1)

    def test_provider_cannot_select_action_outside_server_catalog(self):
        response = self.message(10, "Do something unsupported")
        self.assertIn("could not produce a safe write proposal", response)
        self.assertEqual(len(self.sheet("DraftVouchers")), 1)
        catalog = self.provider.calls[-1]["catalog"]
        self.assertNotIn("finance.voucher.post", catalog)

    def test_approval_token_is_bound_to_same_paired_actor(self):
        response = self.message(11, "Create Ahmad Trading as a company customer")
        token = self.approval_ref(response)

        self.store.add_actor("alpha", "other-owner", role="TENANT_ADMIN")
        self.store.grant("alpha", "other-owner", "customer.create", f"client_api:{SOURCE}")
        code = self.store.create_telegram_pairing_challenge(
            tenant_id="alpha",
            actor_id="other-owner",
            bot_alias=BOT_ALIAS,
            now_epoch=self.now + 20,
        )
        self.ingress.handle(
            bot_alias=BOT_ALIAS,
            headers=tg_headers(),
            body=tg_update(
                12,
                text=f"/pair {code}",
                chat_id=77,
                user_id=77,
            ),
            now_epoch=self.now + 21,
        )
        denied = self.message(13, f"/approve {token}", chat_id=77, user_id=77)
        self.assertIn("belongs to another user", denied)
        self.assertEqual(len(self.sheet("Customers")), 1)

        completed = self.message(14, f"/approve {token}")
        self.assertIn("CHANGE COMPLETED", completed)
        self.assertEqual(len(self.sheet("Customers")), 2)

    def test_model_receives_write_catalog_but_no_trusted_identity_or_path(self):
        self.message(15, "Create Ahmad Trading as a company customer")
        call = self.provider.calls[-1]
        rendered = repr(call)
        self.assertNotIn(str(self.xlsx), rendered)
        self.assertNotIn("alpha", rendered)
        self.assertNotIn("owner", rendered)
        self.assertIn("customer.create", call["catalog"])


if __name__ == "__main__":
    unittest.main()
