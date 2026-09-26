import unittest
from pathlib import Path

from osai.config import RuntimeConfig
from osai.deployment import DeploymentError, evaluate_readiness
from osai.runtime import config_from_env, readiness_from_env


class DeploymentTests(unittest.TestCase):
    def test_offline_runtime_never_claims_external_dependencies_available(self):
        report = evaluate_readiness(
            network_mode="offline",
            required_external=("telegram", "google_drive"),
            configured_external=("telegram", "google_drive"),
        )
        self.assertFalse(report.ready)
        self.assertEqual(
            {item.reason for item in report.dependencies},
            {"offline"},
        )

    def test_unconfigured_and_degraded_dependencies_fail_readiness(self):
        report = evaluate_readiness(
            network_mode="online",
            required_external=("telegram", "llm"),
            configured_external=("telegram",),
            degraded_external=("telegram",),
        )
        self.assertFalse(report.ready)
        states = {item.name: item.reason for item in report.dependencies}
        self.assertEqual(states["telegram"], "provider_unhealthy")
        self.assertEqual(states["llm"], "not_configured")

    def test_unknown_dependency_is_rejected(self):
        with self.assertRaises(DeploymentError):
            evaluate_readiness(
                network_mode="online",
                required_external=("arbitrary-network",),
            )

    def test_same_runtime_loader_accepts_local_and_cloud_contracts(self):
        local = config_from_env(
            {
                "OSAI_PROFILE": "local",
                "OSAI_BIND_HOST": "127.0.0.1",
                "OSAI_DATABASE_PATH": "./var/local.sqlite3",
                "OSAI_SECRET_BACKEND": "test",
            }
        )
        cloud = config_from_env(
            {
                "OSAI_PROFILE": "cloud",
                "OSAI_BIND_HOST": "0.0.0.0",
                "OSAI_DATABASE_PATH": "./var/cloud.sqlite3",
                "OSAI_SECRET_BACKEND": "test",
                "OSAI_PUBLIC_BASE_URL": "https://pilot.example.test",
            }
        )
        self.assertIsInstance(local, RuntimeConfig)
        self.assertIsInstance(cloud, RuntimeConfig)

    def test_runtime_env_offline_is_fail_closed(self):
        report = readiness_from_env(
            {
                "OSAI_NETWORK_MODE": "offline",
                "OSAI_REQUIRED_EXTERNAL": "telegram,google_drive",
                "OSAI_CONFIGURED_EXTERNAL": "telegram,google_drive",
            }
        )
        self.assertFalse(report.ready)

    def test_local_reference_compose_has_no_published_public_port(self):
        compose = Path("deploy/local.compose.yml").read_text(encoding="utf-8")
        self.assertIn("network_mode: host", compose)
        self.assertIn("OSAI_BIND_HOST: 127.0.0.1", compose)
        self.assertNotIn("0.0.0.0:", compose)

    def test_cloud_reference_requires_tls_public_url_from_environment(self):
        compose = Path("deploy/cloud.compose.yml").read_text(encoding="utf-8")
        self.assertIn("OSAI_PUBLIC_BASE_URL: ${OSAI_PUBLIC_BASE_URL:?required}", compose)
        self.assertIn("expose:", compose)
        self.assertNotIn("ports:", compose)


if __name__ == "__main__":
    unittest.main()
