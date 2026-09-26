import tempfile
import unittest
from pathlib import Path

from osai.actions import (
    ActionCoordinator,
    ActionInProgress,
    ActionJournal,
    ActionNotAllowed,
    ActionReceipt,
    ApprovalRequired,
    FinancialInvariantError,
    IdempotencyConflict,
    StaticApprovalVerifier,
    default_action_definitions,
)
from osai.connectors.rest_write import (
    ClientRESTWriteAdapter,
    StaticWriteAuthProvider,
    WriteAccessDenied,
    WriteOperation,
    WriteOperationNotAllowed,
    WriteSource,
    WriteUnavailable,
)
from osai.contracts import ExecutionContext, PolicyDenied
from osai.storage import TenantSecurityStore

SOURCE = "pilot-client"
APPROVAL = "approval:stepup:12345678"


class FakeAdapter:
    def __init__(self):
        self.calls = []
        self.override_actor = None
        self.override_state = None
        self.failure = None

    def execute(self, *, action, payload, context, idempotency_key):
        self.calls.append((action, dict(payload), context, idempotency_key))
        if self.failure is not None:
            raise self.failure
        states = {
            "customer.create": "CREATED",
            "customer.update": "UPDATED",
            "procurement.request.create": "DRAFT",
            "finance.draft_voucher.create": "DRAFT",
        }
        return ActionReceipt(
            action=action,
            source_record_id=f"record-{len(self.calls)}",
            source_state=self.override_state or states[action],
            source_tenant_id=context.tenant_id,
            source_actor_id=self.override_actor or context.actor_id,
            idempotency_key=idempotency_key,
            revision="v1",
        )


class FakeWriteTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def send_json(self, url, *, method, headers, payload, timeout_seconds, max_response_bytes):
        self.calls.append(
            {
                "url": url,
                "method": method,
                "headers": dict(headers),
                "payload": dict(payload),
            }
        )
        return dict(self.response)


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "osai.sqlite3"
        self.store = TenantSecurityStore(self.db)
        self.store.add_tenant("alpha")
        self.store.add_actor("alpha", "owner", role="TENANT_ADMIN")
        self.context = ExecutionContext.issue(
            tenant_id="alpha",
            actor_id="owner",
            resource_scope=(f"client_api:{SOURCE}",),
            session_assurance="telegram-step-up",
        )
        for capability in (
            "customer.create",
            "customer.update",
            "procurement.request.create",
            "finance.draft_voucher.create",
        ):
            self.store.grant("alpha", "owner", capability, f"client_api:{SOURCE}")
        self.journal = ActionJournal(self.db)
        self.adapter = FakeAdapter()
        self.coordinator = ActionCoordinator(
            security_store=self.store,
            journal=self.journal,
            definitions=default_action_definitions(SOURCE),
            adapter=self.adapter,
            approval_verifier=StaticApprovalVerifier(frozenset({APPROVAL})),
        )

    def tearDown(self):
        self.journal.close()
        self.store.close()
        self.tmp.cleanup()

    def execute(self, action, payload, key="idem:12345678", approval=APPROVAL):
        return self.coordinator.execute(
            context=self.context,
            action=action,
            payload=payload,
            approval_ref=approval,
            idempotency_key=key,
        )

    def test_customer_create_is_approved_audited_and_idempotent(self):
        payload = {"name": "Aryana Trading", "customer_type": "COMPANY"}
        first = self.execute("customer.create", payload)
        second = self.execute("customer.create", payload)
        self.assertEqual(first, second)
        self.assertEqual(len(self.adapter.calls), 1)
        events = [item.event for item in self.store.audit_records("alpha")]
        self.assertIn("action.requested", events)
        self.assertIn("action.completed", events)
        self.assertIn("action.idempotent_replay", events)

    def test_idempotency_key_cannot_be_reused_for_different_payload(self):
        self.execute("customer.create", {"name": "A", "customer_type": "COMPANY"})
        with self.assertRaises(IdempotencyConflict):
            self.execute(
                "customer.create",
                {"name": "B", "customer_type": "COMPANY"},
            )
        self.assertEqual(len(self.adapter.calls), 1)

    def test_invalid_approval_blocks_before_source_call(self):
        with self.assertRaises(ApprovalRequired):
            self.execute(
                "customer.create",
                {"name": "A", "customer_type": "COMPANY"},
                approval="approval:wrong:12345678",
            )
        self.assertEqual(self.adapter.calls, [])
        self.assertIn(
            "action.approval_denied",
            [item.event for item in self.store.audit_records("alpha")],
        )

    def test_revoked_actor_cannot_mutate(self):
        self.store.revoke_actor("alpha", "owner")
        with self.assertRaises(PolicyDenied):
            self.execute("customer.create", {"name": "A", "customer_type": "COMPANY"})
        self.assertEqual(self.adapter.calls, [])

    def test_customer_update_requires_a_real_allowed_change(self):
        with self.assertRaises(ActionNotAllowed):
            self.execute(
                "customer.update",
                {"customer_id": "CUS-1", "changes": {}},
            )
        self.assertEqual(self.adapter.calls, [])

    def test_procurement_request_total_is_deterministic(self):
        self.execute(
            "procurement.request.create",
            {
                "requester": "Hash",
                "department": "Admin",
                "currency": "AFN",
                "items": [
                    {
                        "description": "Printer paper",
                        "quantity": "2",
                        "unit_estimated_cost": "1250.50",
                    },
                    {
                        "description": "Toner",
                        "quantity": "1",
                        "unit_estimated_cost": "5000",
                    },
                ],
            },
        )
        sent = self.adapter.calls[0][1]
        self.assertEqual(sent["estimated_total"], "7501.00")

    def test_balanced_draft_voucher_only(self):
        receipt = self.execute(
            "finance.draft_voucher.create",
            {
                "voucher_date": "2026-09-26",
                "currency": "AFN",
                "description": "Test draft",
                "entries": [
                    {"account_code": "6100", "debit": "1000", "credit": "0"},
                    {"account_code": "1100", "debit": "0", "credit": "1000"},
                ],
            },
        )
        self.assertEqual(receipt.source_state, "DRAFT")
        sent = self.adapter.calls[0][1]
        self.assertEqual(sent["entries"][0]["debit"], "1000.00")
        self.assertEqual(sent["entries"][0]["credit"], "0.00")
        self.assertEqual(sent["entries"][1]["debit"], "0.00")
        self.assertEqual(sent["entries"][1]["credit"], "1000.00")
        self.assertEqual(sent["total_debit"], "1000.00")
        self.assertEqual(sent["total_credit"], "1000.00")
        self.assertEqual(sent["status"], "DRAFT")

    def test_unbalanced_or_double_sided_voucher_fails_before_source(self):
        with self.assertRaises(FinancialInvariantError):
            self.execute(
                "finance.draft_voucher.create",
                {
                    "voucher_date": "2026-09-26",
                    "currency": "AFN",
                    "description": "Bad draft",
                    "entries": [
                        {"account_code": "6100", "debit": "1000", "credit": "0"},
                        {"account_code": "1100", "debit": "0", "credit": "900"},
                    ],
                },
            )
        with self.assertRaises(FinancialInvariantError):
            self.execute(
                "finance.draft_voucher.create",
                {
                    "voucher_date": "2026-09-26",
                    "currency": "AFN",
                    "description": "Bad line",
                    "entries": [
                        {"account_code": "6100", "debit": "1000", "credit": "1000"},
                        {"account_code": "1100", "debit": "0", "credit": "1000"},
                    ],
                },
                key="idem:87654321",
            )
        self.assertEqual(self.adapter.calls, [])

    def test_voucher_rejects_more_than_two_decimal_places_before_source(self):
        with self.assertRaises(FinancialInvariantError):
            self.execute(
                "finance.draft_voucher.create",
                {
                    "voucher_date": "2026-09-26",
                    "currency": "AFN",
                    "description": "Precision mismatch",
                    "entries": [
                        {"account_code": "6100", "debit": "100.004", "credit": "0"},
                        {"account_code": "1100", "debit": "0", "credit": "100.00"},
                    ],
                },
            )
        self.assertEqual(self.adapter.calls, [])

    def test_posted_voucher_state_requires_reconciliation(self):
        self.adapter.override_state = "POSTED"
        with self.assertRaises(ActionInProgress):
            self.execute(
                "finance.draft_voucher.create",
                {
                    "voucher_date": "2026-09-26",
                    "currency": "AFN",
                    "description": "Must remain draft",
                    "entries": [
                        {"account_code": "6100", "debit": "1000", "credit": "0"},
                        {"account_code": "1100", "debit": "0", "credit": "1000"},
                    ],
                },
            )

    def test_source_receipt_mismatch_requires_reconciliation_and_blocks_new_key(self):
        self.adapter.override_actor = "someone-else"
        payload = {"name": "A", "customer_type": "COMPANY"}
        with self.assertRaises(ActionInProgress):
            self.execute("customer.create", payload)
        with self.assertRaises(ActionInProgress):
            self.execute("customer.create", payload, key="idem:87654321")
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertIn(
            "action.reconciliation_required",
            [item.event for item in self.store.audit_records("alpha")],
        )

    def test_uncertain_source_failure_blocks_same_mutation_under_new_key(self):
        self.adapter.failure = WriteUnavailable("timeout after dispatch")
        payload = {"name": "A", "customer_type": "COMPANY"}
        with self.assertRaises(ActionInProgress):
            self.execute("customer.create", payload)
        with self.assertRaises(ActionInProgress):
            self.execute("customer.create", payload, key="idem:87654321")
        self.assertEqual(len(self.adapter.calls), 1)
        events = [item.event for item in self.store.audit_records("alpha")]
        self.assertIn("action.reconciliation_required", events)

    def test_unregistered_post_or_delete_action_is_impossible(self):
        with self.assertRaises(ActionNotAllowed):
            self.execute(
                "finance.voucher.post",
                {"voucher_id": "V-1"},
            )


class RestWriteAdapterTests(unittest.TestCase):
    def operation(self):
        return WriteOperation(
            action="customer.create",
            method="POST",
            path="/v1/customers",
            allowed_fields=("name", "customer_type"),
            required_fields=("name", "customer_type"),
            record_id_field="record.id",
            state_field="record.state",
            tenant_field="authorization.tenant_id",
            actor_field="authorization.actor_id",
            idempotency_field="idempotency_key",
            revision_field="record.version",
        )

    def source(self):
        return WriteSource(
            alias=SOURCE,
            base_url="https://sandbox.example.test",
            allowed_hostname="sandbox.example.test",
            operations=(self.operation(),),
        )

    def context(self):
        return ExecutionContext.issue(
            tenant_id="alpha",
            actor_id="owner",
            resource_scope=(f"client_api:{SOURCE}",),
        )

    def test_typed_rest_write_passes_idempotency_and_maps_receipt(self):
        transport = FakeWriteTransport(
            {
                "record": {"id": "CUS-1", "state": "CREATED", "version": "7"},
                "authorization": {"tenant_id": "alpha", "actor_id": "owner"},
                "idempotency_key": "idem:12345678",
            }
        )
        adapter = ClientRESTWriteAdapter(
            connector_id="client-1",
            source=self.source(),
            auth_provider=StaticWriteAuthProvider({"Authorization": "Bearer test"}),
            transport=transport,
        )
        receipt = adapter.execute(
            action="customer.create",
            payload={"name": "A", "customer_type": "COMPANY"},
            context=self.context(),
            idempotency_key="idem:12345678",
        )
        self.assertEqual(receipt.source_record_id, "CUS-1")
        self.assertEqual(receipt.source_actor_id, "owner")
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://sandbox.example.test/v1/customers")
        self.assertEqual(call["headers"]["Idempotency-Key"], "idem:12345678")

    def test_unknown_payload_field_is_denied_before_transport(self):
        transport = FakeWriteTransport({})
        adapter = ClientRESTWriteAdapter(
            connector_id="client-1",
            source=self.source(),
            auth_provider=StaticWriteAuthProvider({"Authorization": "Bearer test"}),
            transport=transport,
        )
        with self.assertRaises(WriteOperationNotAllowed):
            adapter.execute(
                action="customer.create",
                payload={"name": "A", "customer_type": "COMPANY", "url": "https://evil.test"},
                context=self.context(),
                idempotency_key="idem:12345678",
            )
        self.assertEqual(transport.calls, [])

    def test_resource_scope_is_rechecked_before_write(self):
        transport = FakeWriteTransport({})
        adapter = ClientRESTWriteAdapter(
            connector_id="client-1",
            source=self.source(),
            auth_provider=StaticWriteAuthProvider({"Authorization": "Bearer test"}),
            transport=transport,
        )
        wrong = ExecutionContext.issue(
            tenant_id="alpha",
            actor_id="owner",
            resource_scope=("client_api:other",),
        )
        with self.assertRaises(WriteAccessDenied):
            adapter.execute(
                action="customer.create",
                payload={"name": "A", "customer_type": "COMPANY"},
                context=wrong,
                idempotency_key="idem:12345678",
            )
        self.assertEqual(transport.calls, [])

    def test_write_source_rejects_literal_ip(self):
        with self.assertRaises(ValueError):
            WriteSource(
                alias="bad-source",
                base_url="https://127.0.0.1",
                allowed_hostname="127.0.0.1",
                operations=(self.operation(),),
            )


if __name__ == "__main__":
    unittest.main()
