import copy
import json
import tempfile
import unittest
from pathlib import Path

from osai.agent import AgentPlanInvalid, ProviderUnavailable, SourceGroundedAgent
from osai.bot.catalog import CatalogError, catalog_from_mapping
from osai.bot.i18n import detect_language, render_answer
from osai.bot.service import READ_CAPABILITY, BotService, bootstrap_store
from osai.bot.telegram_poll import TelegramPoller
from osai.connectors.google_drive import DriveAccessDenied, drive_table_manifest
from osai.contracts import CapabilityRegistry, SourceProvenance, ToolExecutor, ToolResult
from osai.storage import TenantSecurityStore

EXAMPLE = json.loads(
    (Path(__file__).resolve().parents[1] / "deploy" / "bot" / "catalog.example.json").read_text(encoding="utf-8")
)
CEO_ID = 123456789


class FakeDrive:
    connector_type = "drive"

    def __init__(self):
        self.contexts = []

    def invoke(self, *, capability, arguments, context):
        # Mirrors GoogleDriveConnector._require_context_scope.
        if f"drive:{arguments['resource_alias']}" not in context.resource_scope:
            raise DriveAccessDenied("source resource unavailable")
        self.contexts.append(context)
        return ToolResult(
            data={
                "resource_alias": "finance",
                "table_alias": "expenses",
                "columns": ["Month", "Amount", "Currency"],
                "rows": [["2026-09", "85000", "AFN"], ["2026-09", "18000", "AFN"], ["2026-08", "9000", "AFN"]],
                "row_count": 3,
                "revision": "2026-09-20T10:00:00Z",
                "source_kind": "google_sheet",
            },
            provenance=(
                SourceProvenance.observed(
                    source_id="finance",
                    source_type="google_sheet",
                    revision="2026-09-20T10:00:00Z",
                    locator={"table_alias": "expenses", "sheet_name": "Expenses", "range": "A1:H5000"},
                ),
            ),
        )


class FakePlanner:
    provider_name = "fake"

    def __init__(self, plan=None, error=None):
        self.plan_value = plan
        self.error = error

    def plan(self, *, user_message, tool_catalog, correlation_id):
        if self.error:
            raise self.error
        return self.plan_value


SUM_PLAN = {
    "schema_version": "0.1",
    "action": "tool",
    "capability": "drive.table.read",
    "arguments": {"resource_alias": "finance", "table_alias": "expenses"},
    "analysis": {
        "kind": "sum",
        "column": "Amount",
        "currency_column": "Currency",
        "filters": [{"column": "Month", "equals": "2026-09"}],
    },
}


class BotServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TenantSecurityStore(Path(self.tmp.name) / "osai.sqlite3")
        self.catalog = catalog_from_mapping(EXAMPLE)
        bootstrap_store(self.store, self.catalog)
        self.drive = FakeDrive()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def service(self, planner):
        registry = CapabilityRegistry()
        registry.register(drive_table_manifest(), self.drive)
        agent = SourceGroundedAgent(
            provider=planner,
            registry=registry,
            executor=ToolExecutor(registry),
            policy_check=self.store.authorize,
            audit_sink=self.store,
        )
        return BotService(agent=agent, catalog=self.catalog)

    def test_dari_question_gets_dari_grounded_sum(self):
        reply = self.service(FakePlanner(SUM_PLAN)).handle_text(
            telegram_user_id=CEO_ID, text="مجموع مصارف ماه سنبله چقدر است؟"
        )
        self.assertIn("مجموع Amount: 103,000 AFN", reply)
        self.assertIn("منبع: finance / Expenses", reply)
        self.assertEqual(self.drive.contexts[0].resource_scope, ("drive:finance",))

    def test_pashto_and_english_replies(self):
        service = self.service(FakePlanner(SUM_PLAN))
        self.assertIn("ټولټال", service.handle_text(telegram_user_id=CEO_ID, text="د دې میاشتې ټول لګښتونه څومره دي؟"))
        self.assertIn("Total Amount: 103,000 AFN", service.handle_text(telegram_user_id=CEO_ID, text="total?"))

    def test_unknown_user_is_refused_and_shown_their_id(self):
        reply = self.service(FakePlanner(SUM_PLAN)).handle_text(telegram_user_id=42, text="/start")
        self.assertIn("42", reply)
        self.assertEqual(self.drive.contexts, [])

    def test_start_shows_trilingual_welcome(self):
        reply = self.service(FakePlanner(SUM_PLAN)).handle_text(telegram_user_id=CEO_ID, text="/start")
        self.assertIn("Hello", reply)
        self.assertIn("سلام", reply)

    def test_provider_busy_and_bad_plan_are_localized(self):
        busy = self.service(FakePlanner(error=ProviderUnavailable())).handle_text(telegram_user_id=CEO_ID, text="hi")
        self.assertIn("busy", busy)
        bad = self.service(FakePlanner(error=AgentPlanInvalid())).handle_text(telegram_user_id=CEO_ID, text="سلام")
        self.assertIn("نتوانستم", bad)

    def test_table_outside_role_is_denied(self):
        raw = copy.deepcopy(EXAMPLE)
        raw["resources"].append(
            {
                "alias": "payroll",
                "file_id": "abc",
                "kind": "google_sheet",
                "parent_id": "p",
                "tables": [{"alias": "salaries", "sheet_name": "S", "columns": ["Name", "Salary"]}],
            }
        )
        raw["roles"] = {"ceo": {"resources": ["finance"]}}
        self.catalog = catalog_from_mapping(raw)
        plan = dict(SUM_PLAN, arguments={"resource_alias": "payroll", "table_alias": "salaries"})
        reply = self.service(FakePlanner(plan)).handle_text(telegram_user_id=CEO_ID, text="salaries?")
        self.assertIn("do not have access", reply)

    def test_bootstrap_is_idempotent(self):
        bootstrap_store(self.store, self.catalog)
        self.assertEqual(READ_CAPABILITY, drive_table_manifest().capability)


class CatalogTests(unittest.TestCase):
    def test_example_catalog_is_valid(self):
        catalog = catalog_from_mapping(EXAMPLE)
        self.assertEqual(catalog.scopes_for("ceo"), ("drive:finance",))
        self.assertEqual(catalog.tables[0].columns[0], "Date")

    def test_user_with_unknown_role_rejected(self):
        raw = copy.deepcopy(EXAMPLE)
        raw["users"][0]["role"] = "cfo"
        with self.assertRaises(CatalogError):
            catalog_from_mapping(raw)

    def test_drive_url_instead_of_id_rejected(self):
        raw = copy.deepcopy(EXAMPLE)
        raw["resources"][0]["file_id"] = "https://docs.google.com/spreadsheets/d/x"
        with self.assertRaises(CatalogError):
            catalog_from_mapping(raw)


class I18nTests(unittest.TestCase):
    def test_detect_language(self):
        self.assertEqual(detect_language("مجموع مصارف چقدر است"), "fa")
        self.assertEqual(detect_language("ټول لګښتونه څومره دي"), "ps")
        self.assertEqual(detect_language("total expenses"), "en")

    def test_table_rendering_truncates(self):
        rows = [[str(i), "x"] for i in range(20)]
        text = render_answer("en", {"kind": "table", "columns": ["A", "B"], "rows": rows}, [])
        self.assertIn("20 rows found", text)
        self.assertIn("5 more rows", text)


class FakeTransport:
    def __init__(self, updates):
        self.updates = updates
        self.calls = []

    def post_json(self, url, *, payload, timeout_seconds):
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, payload))
        if method == "getUpdates":
            return {"ok": True, "result": self.updates}
        return {"ok": True, "result": {"message_id": 1}}


TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdef"


class PollerTests(unittest.TestCase):
    def test_private_text_is_answered_and_offset_advances(self):
        transport = FakeTransport(
            [
                {"update_id": 7, "message": {"chat": {"id": 5, "type": "private"}, "from": {"id": 5}, "text": "hi"}},
                {"update_id": 8, "message": {"chat": {"id": -9, "type": "group"}, "from": {"id": 5}, "text": "hi"}},
            ]
        )
        poller = TelegramPoller(token=TOKEN, on_text=lambda uid, text: f"echo {uid} {text}", transport=transport)
        poller.poll_once()
        self.assertEqual(poller.offset, 9)
        sends = [p for m, p in transport.calls if m == "sendMessage"]
        self.assertEqual(sends, [{"chat_id": 5, "text": "echo 5 hi", "disable_web_page_preview": True}])

    def test_voice_without_handler_gets_notice(self):
        transport = FakeTransport(
            [
                {
                    "update_id": 1,
                    "message": {
                        "chat": {"id": 5, "type": "private"},
                        "from": {"id": 5},
                        "voice": {"file_id": "v1", "duration": 3},
                    },
                }
            ]
        )
        TelegramPoller(token=TOKEN, on_text=lambda *a: "", transport=transport).poll_once()
        sends = [p for m, p in transport.calls if m == "sendMessage"]
        self.assertIn("Voice messages are not enabled", sends[0]["text"])

    def test_malformed_token_rejected(self):
        with self.assertRaises(ValueError):
            TelegramPoller(token="not-a-token", on_text=lambda *a: "")


if __name__ == "__main__":
    unittest.main()
