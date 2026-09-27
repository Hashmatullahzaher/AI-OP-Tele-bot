import http.client
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from osai.desktop import app as desktop
from osai.desktop.discovery import build_catalog, discover
from osai.setup_config import MemorySecretStore


def make_xlsx(sheets, *, absolute_targets=True):
    """Minimal real-world-shaped XLSX: sheets = {name: [[cells...], ...]}."""

    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    sheet_xml, rels = [], []
    for index, name in enumerate(sheets, start=1):
        sheet_xml.append(f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"/>')
        target = f"/xl/worksheets/sheet{index}.xml" if absolute_targets else f"worksheets/sheet{index}.xml"
        rels.append(f'<Relationship Id="rId{index}" Type="{rel_ns}/worksheet" Target="{target}"/>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{ns}" xmlns:r="{rel_ns}"><sheets>{"".join(sheet_xml)}</sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels)
            + "</Relationships>",
        )
        for index, rows in enumerate(sheets.values(), start=1):
            body = []
            for r, row in enumerate(rows, start=1):
                cells = []
                for c, value in enumerate(row):
                    ref = f"{chr(65 + c)}{r}"
                    if value is None:
                        continue
                    if isinstance(value, (int, float)):
                        cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                    else:
                        cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>')
                body.append(f'<row r="{r}">{"".join(cells)}</row>')
            z.writestr(
                f"xl/worksheets/sheet{index}.xml",
                f'<worksheet xmlns="{ns}"><sheetData>{"".join(body)}</sheetData></worksheet>',
            )
    return out.getvalue()


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_csv_and_every_xlsx_sheet_with_absolute_targets(self):
        (self.folder / "sales.csv").write_text("Month,Total\n2026-09,10\n", encoding="utf-8")
        (self.folder / "Company 1405.xlsx").write_bytes(
            make_xlsx(
                {
                    "مصارف": [["حساب", "مبلغ"], ["کرایه", 80000], ["تیل", 15500]],
                    "Broken": [["A", None, "C"]],
                }
            )
        )
        (self.folder / "~$Company 1405.xlsx").write_bytes(b"lock")
        result = discover(self.folder)
        found = {(t.file_name, t.sheet_name): t.columns for t in result.tables}
        self.assertEqual(found[("Company 1405.xlsx", "مصارف")], ("حساب", "مبلغ"))
        self.assertEqual(found[("sales.csv", None)], ("Month", "Total"))
        self.assertEqual(len(result.skipped), 1)
        self.assertIn("Broken", result.skipped[0])
        catalog = build_catalog(self.folder, result)
        self.assertEqual({r.alias for r in catalog.local_resources}, {"company-1405", "sales"})
        self.assertEqual(catalog.web_user().actor_id, "owner")

    def test_non_utf8_csv_is_reported(self):
        (self.folder / "old.csv").write_bytes("Name\n\xe9\n".encode("cp1252"))
        result = discover(self.folder)
        self.assertEqual(result.tables, ())
        self.assertIn("UTF-8", result.skipped[0])
        self.assertIsNone(build_catalog(self.folder, result))

    def test_relative_targets_still_work(self):
        (self.folder / "a.xlsx").write_bytes(make_xlsx({"S": [["X"], [1]]}, absolute_targets=False))
        self.assertEqual(discover(self.folder).tables[0].columns, ("X",))


class FakePlanner:
    provider_name = "fake"

    def __init__(self, *args, **kwargs):
        tables = args[1]
        self.table = next(t for t in tables if "مبلغ" in t.columns)

    def plan(self, *, user_message, tool_catalog, correlation_id):
        return {
            "schema_version": "0.1",
            "action": "tool",
            "capability": self.table.capability,
            "arguments": {"resource_alias": self.table.resource_alias, "table_alias": self.table.table_alias},
            "analysis": {"kind": "sum", "column": "مبلغ", "filters": []},
        }


class DesktopAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.app = desktop.DesktopApp(root=base / "home", data_dir=base / "data", secret_store=MemorySecretStore())
        self.app.data_dir.mkdir(parents=True)

    def tearDown(self):
        self.app.close()  # Windows cannot delete an open SQLite file
        self.tmp.cleanup()

    def test_settings_validation_and_key_is_kept_out_of_settings_file(self):
        with self.assertRaisesRegex(ValueError, "API key"):
            self.app.save_settings({"preset": "openrouter", "model": "m:free", "api_key": ""})
        with self.assertRaisesRegex(ValueError, "model"):
            self.app.save_settings({"preset": "openrouter", "model": "", "api_key": "k"})
        with self.assertRaises(ValueError):
            self.app.save_settings({"preset": "nope", "model": "m"})
        self.app.save_settings({"preset": "openrouter", "model": "m:free", "api_key": "sk-secret"})
        self.assertTrue(self.app.configured())
        self.assertNotIn("sk-secret", self.app.settings_path.read_text(encoding="utf-8"))
        # blank key on re-save keeps the stored key
        self.app.save_settings({"preset": "openrouter", "model": "other:free", "api_key": ""})
        self.assertEqual(self.app.secrets.get(desktop.KEY_SECRET), "sk-secret")

    def test_local_model_needs_no_key_and_anthropic_has_default_model(self):
        self.app.save_settings({"preset": "ollama", "model": "qwen2.5:7b"})
        self.assertTrue(self.app.configured())
        self.app.save_settings({"preset": "anthropic", "model": "", "api_key": "k"})
        self.assertTrue(self.app.configured())

    def test_answer_flow(self):
        self.assertIn("no data", self.app.answer("total?")["reply"])
        (self.app.data_dir / "e.xlsx").write_bytes(make_xlsx({"S": [["حساب", "مبلغ"], ["a", 80000], ["b", 15500]]}))
        self.assertIn("not set up", self.app.answer("total?")["reply"])
        self.app.save_settings({"preset": "ollama", "model": "m"})
        with mock.patch.object(desktop, "build_planner", FakePlanner):
            reply = self.app.answer("مجموع مبلغ؟")
        self.assertIn("95,500", reply["reply"])
        self.assertIn("منبع", reply["reply"])

    def test_no_data_message(self):
        self.app.save_settings({"preset": "ollama", "model": "m"})
        self.assertIn("Data folder", self.app.answer("total?")["reply"])


class DesktopServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.app = desktop.DesktopApp(root=base / "home", data_dir=base / "data", secret_store=MemorySecretStore())
        self.server = desktop.build_server(self.app)
        self.opened = []
        self.server.open_folder = self.opened.append
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read().decode("utf-8")
        conn.close()
        return response.status, data

    def post(self, path, payload=None, **headers):
        base = {"Content-Type": "application/json", "X-OSAI-Token": self.server.token}
        base.update(headers)
        return self.request("POST", path, json.dumps(payload or {}), base)

    def test_first_run_shows_settings_then_chat(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Settings", page)
        self.assertIn(self.server.token, page)
        status, body = self.post("/api/settings", {"preset": "ollama", "model": "m"})
        self.assertEqual((status, json.loads(body)), (200, {"ok": True}))
        status, page = self.request("GET", "/")
        self.assertIn("Checking your data folder", page)
        self.assertNotIn('id="settings"', page)

    def test_settings_error_is_returned(self):
        status, body = self.post("/api/settings", {"preset": "openrouter", "model": "m"})
        self.assertEqual(status, 400)
        self.assertIn("API key", json.loads(body)["error"])

    def test_status_and_open_folder(self):
        status, body = self.post("/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["tables"], [])
        self.post("/api/open-data-folder")
        self.assertEqual(self.opened, [self.app.data_dir])

    def test_security_checks(self):
        self.assertEqual(self.post("/api/status", **{"X-OSAI-Token": "wrong"})[0], 403)
        self.assertEqual(self.post("/api/status", Origin="http://evil.example")[0], 403)
        self.assertEqual(self.request("GET", "/", headers={"Host": f"evil.example:{self.port}"})[0], 403)
        self.assertEqual(self.request("GET", "/healthz")[0], 200)

    def test_setup_page_escapes_injected_values(self):
        self.app.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.app.settings_path.write_text(json.dumps({"preset": "custom", "model": "</script><script>x()"}))
        _, page = self.request("GET", "/settings")
        self.assertNotIn("</script><script>x()", page)


if __name__ == "__main__":
    unittest.main()
