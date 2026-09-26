from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from osai.setup_config import (
    JsonSetupStore,
    MemorySecretStore,
    SetupManager,
    SetupSettings,
    SetupValidationError,
    WindowsDpapiSecretStore,
)


class SetupConfigTests(unittest.TestCase):
    def test_settings_round_trip_and_secret_values_are_not_in_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = JsonSetupStore(root / "settings.json")
            secrets = MemorySecretStore()
            manager = SetupManager(store, secrets)
            status = manager.apply(
                settings_raw={
                    "excel_enabled": True,
                    "excel_workbook_path": str((root / "pilot.xlsx").resolve()),
                    "telegram_enabled": True,
                    "telegram_bot_alias": "primary-bot",
                    "llm_provider": "openai",
                    "llm_model": "approved-model",
                    "local_llm_base_url": None,
                },
                telegram_bot_token="123456789:abcdefghijklmnopqrstuvwxyz_ABCDE",
                openai_api_key="sk-test-abcdefghijklmnopqrstuvwxyz",
            )
            self.assertTrue(status.telegram_token_configured)
            self.assertTrue(status.openai_key_configured)
            raw = (root / "settings.json").read_text(encoding="utf-8")
            self.assertNotIn("123456789:", raw)
            self.assertNotIn("sk-test-", raw)
            self.assertEqual(manager.status().settings.llm_provider, "openai")

    def test_enabled_secret_backed_features_require_secrets(self) -> None:
        manager = SetupManager(JsonSetupStore(Path(tempfile.mkdtemp()) / "settings.json"), MemorySecretStore())
        with self.assertRaises(SetupValidationError):
            manager.apply(
                settings_raw={
                    "excel_enabled": False,
                    "excel_workbook_path": None,
                    "telegram_enabled": True,
                    "telegram_bot_alias": "primary-bot",
                    "llm_provider": "none",
                    "llm_model": None,
                    "local_llm_base_url": None,
                },
                telegram_bot_token=None,
                openai_api_key=None,
            )

    def test_local_llm_is_loopback_only(self) -> None:
        with self.assertRaises(SetupValidationError):
            SetupSettings.from_mapping({
                "excel_enabled": False,
                "telegram_enabled": False,
                "telegram_bot_alias": "primary-bot",
                "llm_provider": "local",
                "llm_model": "local-model",
                "local_llm_base_url": "http://192.168.1.10:11434",
            })

    def test_setup_boolean_fields_are_strict(self) -> None:
        with self.assertRaises(SetupValidationError):
            SetupSettings.from_mapping({
                "excel_enabled": "false",
                "telegram_enabled": False,
                "telegram_bot_alias": "primary-bot",
                "llm_provider": "none",
            })

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_dpapi_store_does_not_persist_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.dpapi.json"
            store = WindowsDpapiSecretStore(path)
            store.set("telegram.bot_token", "123456789:abcdefghijklmnopqrstuvwxyz_ABCDE")
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("123456789:", raw)
            self.assertEqual(
                store.get("telegram.bot_token"),
                "123456789:abcdefghijklmnopqrstuvwxyz_ABCDE",
            )
            store.delete("telegram.bot_token")
            self.assertFalse(store.configured("telegram.bot_token"))


if __name__ == "__main__":
    unittest.main()