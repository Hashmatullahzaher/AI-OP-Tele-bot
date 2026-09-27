import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from osai.bot.__main__ import build_service, config_from_env
from osai.bot.catalog import CatalogError, load_catalog
from osai.bot.web import build_web_server
from osai.connectors.google_drive import DriveAccessDenied, DriveSchemaAmbiguous
from osai.connectors.local_files import LOCAL_CAPABILITY, LocalFileConnector, LocalResource, LocalTable
from osai.contracts import ExecutionContext

ROOT = Path(__file__).resolve().parents[1]
LOCAL_CATALOG = ROOT / "deploy" / "bot" / "catalog.local.example.json"


def ctx(*scopes):
    return ExecutionContext.issue(tenant_id="t", actor_id="a", resource_scope=tuple(scopes))


class LocalConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.csv = Path(self.tmp.name) / "data.csv"
        self.csv.write_text("﻿Month,Amount\n2026-09,100\n\n2026-09,50\n", encoding="utf-8")
        self.connector = LocalFileConnector(
            resources={"money": LocalResource("money", self.csv, "csv", (LocalTable("rows"),))}
        )

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, *scopes, table="rows"):
        return self.connector.invoke(
            capability=LOCAL_CAPABILITY,
            arguments={"resource_alias": "money", "table_alias": table},
            context=ctx(*scopes),
        )

    def test_reads_csv_with_bom_and_skips_blank_rows(self):
        result = self.read("local:money")
        self.assertEqual(result.data["columns"], ["Month", "Amount"])
        self.assertEqual(result.data["rows"], [["2026-09", "100"], ["2026-09", "50"]])
        self.assertEqual(result.provenance[0].source_id, "money")

    def test_scope_required(self):
        with self.assertRaises(DriveAccessDenied):
            self.read("local:other")

    def test_duplicate_headers_rejected(self):
        self.csv.write_text("A,a\n1,2\n", encoding="utf-8")
        with self.assertRaises(DriveSchemaAmbiguous):
            self.read("local:money")


class LocalCatalogTests(unittest.TestCase):
    def test_example_local_catalog_resolves_relative_paths(self):
        catalog = load_catalog(LOCAL_CATALOG)
        self.assertEqual(catalog.resources, ())
        self.assertTrue(all(r.path.is_file() for r in catalog.local_resources))
        self.assertEqual(catalog.scopes_for("ceo"), ("local:expenses-file", "local:sales-file"))
        self.assertEqual(catalog.web_user().actor_id, "ceo")
        self.assertEqual({t.capability for t in catalog.tables}, {LOCAL_CAPABILITY})

    def test_web_user_must_exist(self):
        raw = json.loads(LOCAL_CATALOG.read_text(encoding="utf-8"))
        raw["web_user"] = "ghost"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(CatalogError):
                load_catalog(path)


def local_env(tmp, **extra):
    env = {
        "OSAI_CATALOG_PATH": str(LOCAL_CATALOG),
        "OSAI_LLM_PRESET": "ollama",
        "OSAI_LLM_MODEL": "qwen2.5:7b",
        "OSAI_STT": "off",
        "OSAI_DATA_DIR": tmp,
    }
    env.update(extra)
    return env


class LocalConfigTests(unittest.TestCase):
    def test_web_mode_needs_no_telegram_or_google(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = config_from_env(local_env(tmp), need_telegram=False)
            self.assertIsNone(config.telegram_token)
            self.assertIsNone(config.google_key_file)
            service = build_service(config)
            self.assertIn(LOCAL_CAPABILITY, service.agent.registry.names())

    def test_run_mode_still_requires_telegram(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            config_from_env(local_env(tmp))


class WebServerTests(unittest.TestCase):
    def setUp(self):
        self.server = build_web_server(answer=lambda text: f"echo:{text}", user_label="ceo (ceo)", port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read().decode("utf-8")
        conn.close()
        return response.status, data

    def ask(self, text, **headers):
        base = {"Content-Type": "application/json", "X-OSAI-Token": self.server.token}
        base.update(headers)
        return self.request("POST", "/api/ask", json.dumps({"text": text}), base)

    def test_page_embeds_token_and_escapes_user(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(self.server.token, page)

    def test_ask_round_trip(self):
        status, body = self.ask("سلام")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"reply": "echo:سلام"})

    def test_token_origin_and_host_are_enforced(self):
        self.assertEqual(self.ask("x", **{"X-OSAI-Token": "wrong"})[0], 403)
        self.assertEqual(self.ask("x", Origin="http://evil.example")[0], 403)
        self.assertEqual(self.request("GET", "/", headers={"Host": f"evil.example:{self.port}"})[0], 403)

    def test_oversized_and_malformed_bodies(self):
        headers = {"Content-Type": "application/json", "X-OSAI-Token": self.server.token}
        self.assertEqual(self.request("POST", "/api/ask", "x" * 20_000, headers)[0], 413)
        self.assertEqual(self.request("POST", "/api/ask", "{not json", headers)[0], 400)


if __name__ == "__main__":
    unittest.main()
