from __future__ import annotations

import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from osai.config import RuntimeConfig
from osai.runtime import build_server
from osai.windows_service import apply_service_environment


class WindowsPackagingContractTests(unittest.TestCase):
    def test_service_defaults_are_loopback_and_programdata_scoped(self) -> None:
        env = {"PROGRAMDATA": str(Path("sandbox") / "ProgramData")}
        apply_service_environment(env)

        self.assertEqual(env["OSAI_PROFILE"], "local")
        self.assertEqual(env["OSAI_BIND_HOST"], "127.0.0.1")
        self.assertEqual(env["OSAI_PORT"], "8765")
        self.assertEqual(env["OSAI_NETWORK_MODE"], "offline")
        self.assertEqual(env["OSAI_SECRET_BACKEND"], "os_keyring")
        self.assertTrue(env["OSAI_DATABASE_PATH"].endswith(str(Path("OS AI Core") / "data" / "osai.sqlite3")))
        self.assertFalse(any("TOKEN" in key or "API_KEY" in key for key in env))

    def test_operator_dashboard_is_served_by_real_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = RuntimeConfig.from_mapping(
                {
                    "profile": "local",
                    "bind_host": "127.0.0.1",
                    "port": 0,
                    "database_path": str(Path(temp_dir) / "osai.sqlite3"),
                    "secret_backend": "test",
                    "outbound_hosts": [],
                }
            )
            server = build_server(config)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                port = server.server_address[1]
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
                    body = response.read().decode("utf-8")
                    self.assertIn("Local Operator Dashboard", body)
                    self.assertNotIn("DEMO ONLY", body)
                    self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                    self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as response:
                    self.assertIn('"status":"alive"', response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
