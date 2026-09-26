from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from osai.portable_app import apply_portable_environment, portable_data_root


class PortableLocalTests(unittest.TestCase):
    def test_portable_defaults_are_per_user_and_loopback_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"LOCALAPPDATA": tmp}
            apply_portable_environment(env)
            self.assertEqual(env["OSAI_PROFILE"], "local")
            self.assertEqual(env["OSAI_BIND_HOST"], "127.0.0.1")
            self.assertEqual(env["OSAI_PORT"], "8765")
            self.assertEqual(env["OSAI_NETWORK_MODE"], "offline")
            self.assertEqual(env["OSAI_SECRET_BACKEND"], "os_keyring")
            self.assertEqual(
                Path(env["OSAI_DATABASE_PATH"]),
                Path(tmp) / "OS AI Core Portable" / "data" / "osai.sqlite3",
            )

    def test_portable_data_root_uses_localappdata(self) -> None:
        env = {"LOCALAPPDATA": r"C:\Users\Hash\AppData\Local"}
        root = portable_data_root(env)
        self.assertTrue(str(root).endswith("OS AI Core Portable"))


if __name__ == "__main__":
    unittest.main()
