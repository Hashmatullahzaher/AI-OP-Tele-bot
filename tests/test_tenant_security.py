import json
from pathlib import Path
import tempfile
import unittest

from osai.contracts import CapabilityManifest, ExecutionContext, PolicyDenied
from osai.storage import TenantSecurityStore


def manifest():
    return CapabilityManifest.from_dict({
        "schema_version": "0.1",
        "connector_type": "drive",
        "capability": "drive.sheet.read",
        "action": "READ",
        "input_schema": {
            "type": "object",
            "properties": {"resource": {"type": "string"}},
            "required": ["resource"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"rows": {"type": "integer"}},
            "required": ["rows"],
            "additionalProperties": False,
        },
        "required_scopes": ["drive:read"],
        "resource_allowlist_ref": "policyref:drive",
        "mutates": False,
        "timeout_seconds": 15,
    })


class TenantSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "osai.sqlite3")
        self.store = TenantSecurityStore(self.db)
        for tenant in ("alpha", "beta"):
            self.store.add_tenant(tenant)
            self.store.add_actor(tenant, f"{tenant}-admin")
        self.alpha = ExecutionContext.issue(
            tenant_id="alpha",
            actor_id="alpha-admin",
            resource_scope=("drive:pilot",),
        )
        self.beta = ExecutionContext.issue(
            tenant_id="beta",
            actor_id="beta-admin",
            resource_scope=("drive:pilot",),
        )
        self.store.grant("alpha", "alpha-admin", "drive.sheet.read", "drive:pilot")
        self.store.grant("beta", "beta-admin", "drive.sheet.read", "drive:pilot")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_policy_allows_exact_tenant_scope(self):
        self.store.authorize(self.alpha, manifest())

    def test_cross_tenant_report_and_cache_are_denied(self):
        self.store.put_scoped(tenant_id="beta", kind="report", object_id="r1", payload={"name": "secret"})
        self.store.put_scoped(tenant_id="beta", kind="cache", object_id="c1", payload={"value": 9})
        with self.assertRaises(PolicyDenied):
            self.store.get_scoped(context=self.alpha, owner_tenant_id="beta", kind="report", object_id="r1")
        with self.assertRaises(PolicyDenied):
            self.store.get_scoped(context=self.alpha, owner_tenant_id="beta", kind="cache", object_id="c1")

    def test_cross_tenant_credential_ref_is_denied(self):
        self.store.register_connector(
            tenant_id="beta",
            connector_id="drive-1",
            connector_type="drive",
            credential_ref="secretref:beta-drive",
        )
        with self.assertRaises(PolicyDenied):
            self.store.credential_ref(context=self.alpha, owner_tenant_id="beta", connector_id="drive-1")

    def test_only_opaque_secret_refs_are_accepted(self):
        with self.assertRaises(ValueError):
            self.store.register_connector(
                tenant_id="alpha",
                connector_id="drive-bad",
                connector_type="drive",
                credential_ref="ya29.real-looking-token",
            )

    def test_revoked_actor_fails_authorization_and_scoped_read(self):
        self.store.put_scoped(tenant_id="alpha", kind="report", object_id="r1", payload={"ok": True})
        self.store.revoke_actor("alpha", "alpha-admin")
        with self.assertRaises(PolicyDenied):
            self.store.authorize(self.alpha, manifest())
        with self.assertRaises(PolicyDenied):
            self.store.get_scoped(context=self.alpha, owner_tenant_id="alpha", kind="report", object_id="r1")

    def test_audit_is_persistent_redacted_and_chain_verified_after_restart(self):
        seq = self.store.append_audit(
            context=self.alpha,
            event="drive.read",
            capability="drive.sheet.read",
            resource_scope="drive:pilot",
            result="OK",
            sensitive_payload={"customer_name": "do-not-log-raw"},
        )
        self.assertGreater(seq, 0)
        row = self.store._db.execute("SELECT * FROM audit_events WHERE sequence=?", (seq,)).fetchone()
        self.assertNotIn("do-not-log-raw", json.dumps(dict(row)))
        self.assertTrue(self.store.verify_audit_chain("alpha"))
        self.store.close()
        self.store = TenantSecurityStore(self.db)
        records = self.store.audit_records("alpha")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].event, "drive.read")
        self.assertTrue(self.store.verify_audit_chain("alpha"))

    def test_tenant_audit_queries_do_not_cross(self):
        self.store.append_audit(context=self.alpha, event="alpha.read", result="OK")
        self.store.append_audit(context=self.beta, event="beta.read", result="OK")
        self.assertEqual([r.event for r in self.store.audit_records("alpha")], ["alpha.read"])
        self.assertEqual([r.event for r in self.store.audit_records("beta")], ["beta.read"])


if __name__ == "__main__":
    unittest.main()
