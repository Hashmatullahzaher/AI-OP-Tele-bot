from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from osai.portable_app import (
    RESERVED_PORTS,
    apply_portable_environment,
    portable_data_root,
)


class PortableLocalTests(unittest.TestCase):
    def test_portable_defaults_are_per_user_loopback_and_dynamic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"LOCALAPPDATA": tmp}
            apply_portable_environment(env)
            port = int(env["OSAI_PORT"])
            self.assertEqual(env["OSAI_PROFILE"], "local")
            self.assertEqual(env["OSAI_BIND_HOST"], "127.0.0.1")
            self.assertGreaterEqual(port, 1024)
            self.assertLessEqual(port, 65535)
            self.assertNotIn(port, RESERVED_PORTS)
            self.assertEqual(env["OSAI_NETWORK_MODE"], "offline")
            self.assertEqual(env["OSAI_SECRET_BACKEND"], "os_keyring")
            self.assertEqual(
                Path(env["OSAI_DATABASE_PATH"]),
                Path(tmp) / "OS AI Core Portable" / "data" / "osai.sqlite3",
            )

    def test_reserved_ports_are_rejected_even_when_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for port in sorted(RESERVED_PORTS):
                env = {"LOCALAPPDATA": tmp, "OSAI_PORT": str(port)}
                with self.assertRaisesRegex(RuntimeError, "reserved port"):
                    apply_portable_environment(env)

    def test_portable_data_root_uses_localappdata(self) -> None:
        env = {"LOCALAPPDATA": r"C:\Users\Hash\AppData\Local"}
        root = portable_data_root(env)
        self.assertTrue(str(root).endswith("OS AI Core Portable"))


if __name__ == "__main__":
    unittest.main()
