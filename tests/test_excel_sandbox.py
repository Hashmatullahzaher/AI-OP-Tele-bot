import tempfile
import unittest
from pathlib import Path

from osai.actions import (
    ActionCoordinator,
    ActionJournal,
    StaticApprovalVerifier,
    default_action_definitions,
)
from osai.connectors.local_excel import (
    ExcelSandboxWriteAdapter,
    ExcelTable,
    LocalExcelAccessDenied,
    LocalExcelConnector,
    LocalExcelResource,
    excel_table_manifest,
    initialize_excel_sandbox,
)
from osai.contracts import CapabilityRegistry, ExecutionContext, ToolExecutor
from osai.storage import TenantSecurityStore

SOURCE = "excel-pilot"
RESOURCE = "excel-uat"
APPROVAL = "approval:excel:12345678"


class ExcelSandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.xlsx = root / "OS-AI-Core-Excel-UAT.xlsx"
        self.db = root / "osai.sqlite3"
        initialize_excel_sandbox(self.xlsx)

        self.store = TenantSecurityStore(self.db)
        self.store.add_tenant("alpha")
        self.store.add_actor("alpha", "owner", role="TENANT_ADMIN")
        for capability in (
            "customer.create",
            "customer.update",
            "procurement.request.create",
            "finance.draft_voucher.create",
        ):
            self.store.grant("alpha", "owner", capability, f"client_api:{SOURCE}")

        self.context = ExecutionContext.issue(
            tenant_id="alpha",
            actor_id="owner",
            resource_scope=(f"client_api:{SOURCE}",),
            session_assurance="excel-uat-step-up",
        )
        self.journal = ActionJournal(self.db)
        self.adapter = ExcelSandboxWriteAdapter(
            workbook_path=self.xlsx,
            source_alias=SOURCE,
        )
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

    def execute(self, action, payload, *, key):
        return self.coordinator.execute(
            context=self.context,
            action=action,
            payload=payload,
            approval_ref=APPROVAL,
            idempotency_key=key,
        )

    def read(self, table_alias, sheet_name):
        resource = LocalExcelResource(
            alias=RESOURCE,
            workbook_path=str(self.xlsx),
            tables=(ExcelTable(alias=table_alias, sheet_name=sheet_name),),
        )
        connector = LocalExcelConnector(resource=resource)
        context = ExecutionContext.issue(
            tenant_id="alpha",
            actor_id="owner",
            resource_scope=(f"excel:{RESOURCE}",),
        )
        registry = CapabilityRegistry()
        registry.register(excel_table_manifest(), connector)
        return ToolExecutor(registry).execute(
            capability="excel.table.read",
            arguments={"resource_alias": RESOURCE, "table_alias": table_alias},
            context=context,
        )

    def test_create_and_update_customer_are_visible_in_excel(self):
        receipt = self.execute(
            "customer.create",
            {
                "name": "Aryana Trading",
                "customer_type": "COMPANY",
                "phone": "0700000000",
            },
            key="idem:excel:cust:0001",
        )
        self.assertEqual(receipt.source_record_id, "CUS-000001")

        self.execute(
            "customer.update",
            {
                "customer_id": "CUS-000001",
                "changes": {"phone": "0799999999", "address": "Mazar-e-Sharif"},
            },
            key="idem:excel:cust:0002",
        )

        result = self.read("customers", "Customers")
        self.assertEqual(result.data["row_count"], 1)
        row = result.data["rows"][0]
        self.assertEqual(row[0], "CUS-000001")
        self.assertEqual(row[3], "0799999999")
        self.assertEqual(row[5], "Mazar-e-Sharif")
        self.assertEqual(row[7], "2")

    def test_procurement_request_and_items_are_written(self):
        receipt = self.execute(
            "procurement.request.create",
            {
                "requester": "Hash",
                "department": "Admin",
                "currency": "AFN",
                "justification": "Office supplies",
                "items": [
                    {
                        "description": "Paper",
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
            key="idem:excel:pr:0001",
        )
        self.assertEqual(receipt.source_record_id, "PR-000001")

        requests = self.read("prs", "ProcurementRequests")
        items = self.read("pr_items", "ProcurementItems")
        self.assertEqual(requests.data["rows"][0][5], "7501.00")
        self.assertEqual(requests.data["rows"][0][6], "DRAFT")
        self.assertEqual(items.data["row_count"], 2)
        self.assertEqual(items.data["rows"][1][5], "5000.00")

    def test_balanced_draft_voucher_is_written_as_draft(self):
        receipt = self.execute(
            "finance.draft_voucher.create",
            {
                "voucher_date": "2026-09-26",
                "currency": "AFN",
                "description": "Office supplies payment",
                "entries": [
                    {
                        "account_code": "6300",
                        "debit": "9000",
                        "credit": "0",
                        "memo": "Expense",
                    },
                    {
                        "account_code": "1100",
                        "debit": "0",
                        "credit": "9000",
                        "memo": "Cash",
                    },
                ],
            },
            key="idem:excel:dv:0001",
        )
        self.assertEqual(receipt.source_record_id, "DV-000001")
        self.assertEqual(receipt.source_state, "DRAFT")

        vouchers = self.read("vouchers", "DraftVouchers")
        entries = self.read("voucher_entries", "VoucherEntries")
        self.assertEqual(vouchers.data["rows"][0][4], "9000.00")
        self.assertEqual(vouchers.data["rows"][0][5], "9000.00")
        self.assertEqual(vouchers.data["rows"][0][6], "DRAFT")
        self.assertEqual(entries.data["row_count"], 2)

    def test_source_side_idempotency_prevents_duplicate_row(self):
        payload = {"name": "Sadaf Group", "customer_type": "COMPANY"}
        first = self.adapter.execute(
            action="customer.create",
            payload=payload,
            context=self.context,
            idempotency_key="idem:excel:source:001",
        )
        second = self.adapter.execute(
            action="customer.create",
            payload=payload,
            context=self.context,
            idempotency_key="idem:excel:source:001",
        )
        self.assertEqual(first, second)
        result = self.read("customers", "Customers")
        self.assertEqual(result.data["row_count"], 1)

    def test_formula_like_text_is_stored_as_plain_excel_text(self):
        self.execute(
            "customer.create",
            {
                "name": "=1+1",
                "customer_type": "COMPANY",
                "address": "+SUM(A1:A2)",
            },
            key="idem:excel:text:0001",
        )
        result = self.read("customers", "Customers")
        self.assertEqual(result.data["rows"][0][1], "=1+1")
        self.assertEqual(result.data["rows"][0][5], "+SUM(A1:A2)")

    def test_read_scope_is_enforced(self):
        resource = LocalExcelResource(
            alias=RESOURCE,
            workbook_path=str(self.xlsx),
            tables=(ExcelTable(alias="customers", sheet_name="Customers"),),
        )
        connector = LocalExcelConnector(resource=resource)
        wrong = ExecutionContext.issue(
            tenant_id="alpha",
            actor_id="owner",
            resource_scope=("excel:other",),
        )
        with self.assertRaises(LocalExcelAccessDenied):
            connector.invoke(
                capability="excel.table.read",
                arguments={"resource_alias": RESOURCE, "table_alias": "customers"},
                context=wrong,
            )


if __name__ == "__main__":
    unittest.main()
