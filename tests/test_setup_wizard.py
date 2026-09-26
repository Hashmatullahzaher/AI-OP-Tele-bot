from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from osai.config import RuntimeConfig
from osai.runtime import build_server, startup_check


class SetupWizardHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = RuntimeConfig.from_mapping({
            "profile": "local",
            "bind_host": "127.0.0.1",
            "port": 0,
            "database_path": str(root / "data" / "osai.sqlite3"),
            "secret_backend": "test",
            "outbound_hosts": [],
        })
        startup_check(self.config)
        self.server = build_server(self.config)
        self.port = int(self.server.server_address[1])
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)
        self.tmp.cleanup()

    def request(self, method: str, path: str, body: dict | None = None, *, csrf: str | None = None, origin: str | None = None, host: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        else:
            payload = None
        if csrf is not None:
            headers["X-OSAI-CSRF"] = csrf
        if origin is not None:
            headers["Origin"] = origin
        if host is not None:
            headers["Host"] = host
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        content_type = response.getheader("Content-Type") or ""
        connection.close()
        if "application/json" in content_type:
            return status, json.loads(raw.decode())
        return status, raw.decode()

    def csrf(self) -> str:
        controller = getattr(self.server, "setup_controller")
        return controller.csrf_token

    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def test_setup_page_and_status_never_return_secret_values(self) -> None:
        status, page = self.request("GET", "/setup")
        self.assertEqual(status, 200)
        self.assertIn("OS AI Core Setup", page)
        status, payload = self.request("GET", "/api/setup/status")
        self.assertEqual(status, 200)
        self.assertFalse(payload["telegram_token_configured"])
        self.assertFalse(payload["openai_key_configured"])
        self.assertNotIn("telegram_bot_token", payload)
        self.assertNotIn("openai_api_key", payload)

    def test_setup_mutation_requires_csrf_and_same_origin(self) -> None:
        status, _ = self.request(
            "POST", "/api/setup/excel/init", {}, origin=self.origin()
        )
        self.assertEqual(status, 403)
        status, _ = self.request(
            "POST", "/api/setup/excel/init", {}, csrf=self.csrf(), origin="http://evil.example"
        )
        self.assertEqual(status, 403)

    def test_excel_and_credentials_can_be_configured_without_secret_echo(self) -> None:
        status, excel = self.request(
            "POST",
            "/api/setup/excel/init",
            {},
            csrf=self.csrf(),
            origin=self.origin(),
        )
        self.assertEqual(status, 200)
        self.assertTrue(excel["managed_excel_exists"])
        workbook = Path(excel["managed_excel_path"])
        self.assertTrue(workbook.exists())

        request = {
            "settings": {
                "excel_enabled": True,
                "excel_workbook_path": str(workbook),
                "telegram_enabled": True,
                "telegram_bot_alias": "primary-bot",
                "llm_provider": "openai",
                "llm_model": "approved-model",
                "local_llm_base_url": None,
            },
            "telegram_bot_token": "123456789:abcdefghijklmnopqrstuvwxyz_ABCDE",
            "openai_api_key": "sk-test-abcdefghijklmnopqrstuvwxyz",
            "clear_telegram_token": False,
            "clear_openai_api_key": False,
        }
        status, response = self.request(
            "POST", "/api/setup", request, csrf=self.csrf(), origin=self.origin()
        )
        self.assertEqual(status, 200)
        self.assertTrue(response["telegram_token_configured"])
        self.assertTrue(response["openai_key_configured"])
        self.assertTrue(response["setup_complete"])
        rendered = json.dumps(response)
        self.assertNotIn("123456789:", rendered)
        self.assertNotIn("sk-test-", rendered)

        status, persisted = self.request("GET", "/api/setup/status")
        self.assertEqual(status, 200)
        self.assertTrue(persisted["telegram_token_configured"])
        self.assertTrue(persisted["openai_key_configured"])
        self.assertFalse(persisted["telegram_connector_active"])
        self.assertFalse(persisted["llm_connector_active"])

    def test_wrong_host_and_invalid_boolean_fail_closed(self) -> None:
        status, _ = self.request("GET", "/setup", host="evil.example")
        self.assertEqual(status, 404)
        status, response = self.request(
            "POST",
            "/api/setup",
            {"settings": {"excel_enabled": "false", "telegram_enabled": False, "telegram_bot_alias": "primary-bot", "llm_provider": "none"}},
            csrf=self.csrf(),
            origin=self.origin(),
        )
        self.assertEqual(status, 400)
        self.assertIn("boolean", response["error"])


if __name__ == "__main__":
    unittest.main()