import unittest

from osai.config import RuntimeConfig
from osai.contracts import (
    CapabilityManifest,
    CapabilityNotAllowed,
    CapabilityRegistry,
    ContractError,
    ExecutionContext,
    SchemaValidationError,
    SourceProvenance,
    ToolExecutor,
    ToolResult,
)


def manifest_dict():
    return {
        "schema_version": "0.1",
        "connector_type": "test",
        "capability": "sales.total.read",
        "action": "READ",
        "input_schema": {
            "type": "object",
            "properties": {"month": {"type": "string", "minLength": 7, "maxLength": 7}},
            "required": ["month"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "string"},
                "currency": {"type": "string", "enum": ["AFN"]},
                "sources": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["amount", "currency", "sources"],
            "additionalProperties": False,
        },
        "required_scopes": ["sales:read"],
        "resource_allowlist_ref": "policyref:test-sales",
        "mutates": False,
        "timeout_seconds": 15,
    }


class FakeConnector:
    connector_type = "test"

    def invoke(self, *, capability, arguments, context):
        self.last_context = context
        return ToolResult(
            data={"amount": "125.00", "currency": "AFN", "sources": ["fixture-1"]},
            provenance=(SourceProvenance.observed(source_id="fixture-1", source_type="test"),),
        )


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.manifest = CapabilityManifest.from_dict(manifest_dict())
        self.connector = FakeConnector()
        self.registry = CapabilityRegistry()
        self.registry.register(self.manifest, self.connector)
        self.executor = ToolExecutor(self.registry)
        self.context = ExecutionContext.issue(tenant_id="t1", actor_id="u1", resource_scope=("sales",))

    def test_unregistered_capability_rejected(self):
        with self.assertRaises(CapabilityNotAllowed):
            self.executor.execute(capability="admin.sql.run", arguments={}, context=self.context)

    def test_unknown_input_field_rejected_before_connector(self):
        with self.assertRaises(SchemaValidationError):
            self.executor.execute(
                capability="sales.total.read",
                arguments={"month": "2026-09", "sql": "select *"},
                context=self.context,
            )

    def test_required_input_field_rejected(self):
        with self.assertRaises(SchemaValidationError):
            self.executor.execute(capability="sales.total.read", arguments={}, context=self.context)

    def test_registered_capability_returns_provenance(self):
        result = self.executor.execute(
            capability="sales.total.read",
            arguments={"month": "2026-09"},
            context=self.context,
        )
        self.assertEqual(result.data["amount"], "125.00")
        self.assertEqual(result.provenance[0].source_id, "fixture-1")
        self.assertEqual(self.connector.last_context.tenant_id, "t1")

    def test_manifest_rejects_unknown_top_level_field(self):
        raw = manifest_dict()
        raw["dangerous"] = True
        with self.assertRaises(ContractError):
            CapabilityManifest.from_dict(raw)

    def test_output_schema_is_enforced(self):
        class BadConnector:
            connector_type = "test"
            def invoke(self, *, capability, arguments, context):
                return ToolResult(data={"amount": "12", "currency": "USD", "sources": []})
        registry = CapabilityRegistry()
        registry.register(self.manifest, BadConnector())
        with self.assertRaises(SchemaValidationError):
            ToolExecutor(registry).execute(
                capability="sales.total.read",
                arguments={"month": "2026-09"},
                context=self.context,
            )

    def test_same_contract_suite_accepts_local_and_cloud_profiles(self):
        configs = [
            RuntimeConfig.from_mapping({
                "profile": "local",
                "bind_host": "127.0.0.1",
                "database_path": "./var/local.sqlite3",
                "secret_backend": "test",
                "outbound_hosts": ["www.googleapis.com"],
            }),
            RuntimeConfig.from_mapping({
                "profile": "cloud",
                "bind_host": "0.0.0.0",
                "database_path": "./var/cloud.sqlite3",
                "secret_backend": "test",
                "public_base_url": "https://pilot.example.test",
                "outbound_hosts": ["www.googleapis.com"],
            }),
        ]
        for config in configs:
            with self.subTest(profile=config.profile):
                self.assertIn("www.googleapis.com", config.outbound_hosts)
                result = self.executor.execute(
                    capability="sales.total.read",
                    arguments={"month": "2026-09"},
                    context=self.context,
                )
                self.assertEqual(result.data["currency"], "AFN")

    def test_local_profile_cannot_bind_publicly(self):
        with self.assertRaises(ContractError):
            RuntimeConfig.from_mapping({"profile": "local", "bind_host": "0.0.0.0"})

    def test_cloud_profile_requires_https_public_url(self):
        with self.assertRaises(ContractError):
            RuntimeConfig.from_mapping({"profile": "cloud", "public_base_url": "http://example.test"})


if __name__ == "__main__":
    unittest.main()
