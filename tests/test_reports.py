import csv
import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from osai.contracts import ExecutionContext
from osai.reports import (
    ReportAccessDenied,
    ReportArtifactStore,
    ReportExpired,
    ReportIntegrityError,
    ReportSchemaError,
    ReportSource,
    ReportSpec,
)
from osai.storage import TenantSecurityStore


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.tenants = TenantSecurityStore(root / "tenant.sqlite3")
        self.tenants.add_tenant("alpha")
        self.tenants.add_actor("alpha", "alice", role="TENANT_ADMIN")
        self.tenants.add_actor("alpha", "bob", role="TENANT_USER")
        self.tenants.add_tenant("beta")
        self.tenants.add_actor("beta", "mallory", role="TENANT_ADMIN")
        self.store = ReportArtifactStore(
            root_dir=root / "artifacts",
            metadata_db=root / "reports.sqlite3",
            tenant_store=self.tenants,
        )
        self.alice = ExecutionContext.issue(tenant_id="alpha", actor_id="alice")
        self.bob = ExecutionContext.issue(tenant_id="alpha", actor_id="bob")
        self.mallory = ExecutionContext.issue(tenant_id="beta", actor_id="mallory")
        self.spec = ReportSpec(
            title="Project Expenses",
            columns=("Date", "ProjectCode", "Amount", "Currency"),
            rows=(
                ("2026-09-01", "PRJ-001", "1000.50", "AFN"),
                ("2026-09-02", "PRJ-001", "2000", "AFN"),
            ),
            sources=(
                ReportSource(
                    source_id="pilot-finance-sheet",
                    source_type="google_sheet",
                    revision="2026-09-26T03:00:00Z",
                    locator={"sheet": "Transactions", "range": "A1:I10"},
                ),
            ),
            period_start="2026-09-01",
            period_end="2026-09-30",
            currency_column="Currency",
            amount_column="Amount",
        )

    def tearDown(self):
        self.store.close()
        self.tenants.close()
        self.tmp.cleanup()

    def test_create_csv_xlsx_pdf_and_exact_filenames(self):
        artifacts = self.store.create(context=self.alice, spec=self.spec, now_epoch=1000)
        self.assertEqual([a.format for a in artifacts], ["csv", "xlsx", "pdf"])
        self.assertEqual(
            [a.filename for a in artifacts],
            [
                "project-expenses_2026-09-01_to_2026-09-30.csv",
                "project-expenses_2026-09-01_to_2026-09-30.xlsx",
                "project-expenses_2026-09-01_to_2026-09-30.pdf",
            ],
        )
        for artifact in artifacts:
            self.assertEqual(artifact.row_count, 2)
            self.assertEqual(artifact.total_amount, "3000.5")
            self.assertEqual(artifact.currency, "AFN")
            self.assertEqual(artifact.source_revisions, ("2026-09-26T03:00:00Z",))

    def test_csv_row_coverage_and_money_are_exact(self):
        artifact = self.store.create(context=self.alice, spec=self.spec, formats=("csv",), now_epoch=1000)[0]
        _, payload = self.store.download(context=self.alice, report_id=artifact.report_id, format="csv", now_epoch=1001)
        text = payload.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0], list(self.spec.columns))
        self.assertEqual(rows[1:], [list(r) for r in self.spec.rows])
        self.assertEqual(hashlib.sha256(payload).hexdigest(), artifact.sha256)

    def test_xlsx_is_native_zip_and_preserves_rows(self):
        artifact = self.store.create(context=self.alice, spec=self.spec, formats=("xlsx",), now_epoch=1000)[0]
        _, payload = self.store.download(context=self.alice, report_id=artifact.report_id, format="xlsx", now_epoch=1001)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertIn("xl/workbook.xml", archive.namelist())
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("ProjectCode", sheet)
        self.assertIn("1000.50", sheet)
        self.assertIn("AFN", sheet)

    def test_pdf_has_native_header_and_source_revision(self):
        artifact = self.store.create(context=self.alice, spec=self.spec, formats=("pdf",), now_epoch=1000)[0]
        _, payload = self.store.download(context=self.alice, report_id=artifact.report_id, format="pdf", now_epoch=1001)
        self.assertTrue(payload.startswith(b"%PDF-1.4"))
        self.assertIn(b"pilot-finance-sheet@2026-09-26T03:00:00Z", payload)

    def test_other_tenant_and_other_actor_cannot_download(self):
        artifact = self.store.create(context=self.alice, spec=self.spec, formats=("csv",), now_epoch=1000)[0]
        for context in (self.mallory, self.bob):
            with self.subTest(actor=context.actor_id):
                with self.assertRaises(ReportAccessDenied):
                    self.store.download(context=context, report_id=artifact.report_id, format="csv", now_epoch=1001)

    def test_revoked_creator_cannot_download(self):
        artifact = self.store.create(context=self.alice, spec=self.spec, formats=("csv",), now_epoch=1000)[0]
        self.tenants.revoke_actor("alpha", "alice")
        with self.assertRaises(ReportAccessDenied):
            self.store.download(context=self.alice, report_id=artifact.report_id, format="csv", now_epoch=1001)

    def test_expired_link_fails_closed(self):
        artifact = self.store.create(
            context=self.alice, spec=self.spec, formats=("csv",), now_epoch=1000, ttl_seconds=60
        )[0]
        with self.assertRaises(ReportExpired):
            self.store.download(context=self.alice, report_id=artifact.report_id, format="csv", now_epoch=1060)

    def test_tampered_artifact_is_detected(self):
        artifact = self.store.create(context=self.alice, spec=self.spec, formats=("csv",), now_epoch=1000)[0]
        row = self.store._db.execute(
            "SELECT relative_path FROM report_artifacts WHERE report_id=? AND format='csv'", (artifact.report_id,)
        ).fetchone()
        (self.store.root / row["relative_path"]).write_bytes(b"tampered")
        with self.assertRaises(ReportIntegrityError):
            self.store.download(context=self.alice, report_id=artifact.report_id, format="csv", now_epoch=1001)

    def test_mixed_currency_and_non_numeric_money_fail_closed(self):
        mixed = ReportSpec(
            title="Mixed",
            columns=("Amount", "Currency"),
            rows=(("10", "AFN"), ("5", "USD")),
            sources=self.spec.sources,
            period_start="2026-09-01",
            period_end="2026-09-30",
            amount_column="Amount",
            currency_column="Currency",
        )
        with self.assertRaises(ReportSchemaError):
            self.store.create(context=self.alice, spec=mixed, formats=("csv",), now_epoch=1000)
        invalid = ReportSpec(
            title="Invalid",
            columns=("Amount", "Currency"),
            rows=(("not-money", "AFN"),),
            sources=self.spec.sources,
            period_start="2026-09-01",
            period_end="2026-09-30",
            amount_column="Amount",
            currency_column="Currency",
        )
        with self.assertRaises(ReportSchemaError):
            self.store.create(context=self.alice, spec=invalid, formats=("csv",), now_epoch=1000)

    def test_pdf_unicode_fails_closed_until_font_is_configured(self):
        unicode_spec = ReportSpec(
            title="گزارش مالی",
            columns=("Name",),
            rows=(("test",),),
            sources=self.spec.sources,
            period_start="2026-09-01",
            period_end="2026-09-30",
        )
        with self.assertRaises(ReportSchemaError):
            self.store.create(context=self.alice, spec=unicode_spec, formats=("pdf",), now_epoch=1000)

    def test_audit_records_create_and_download_without_payload(self):
        artifact = self.store.create(context=self.alice, spec=self.spec, formats=("csv",), now_epoch=1000)[0]
        self.store.download(context=self.alice, report_id=artifact.report_id, format="csv", now_epoch=1001)
        events = self.tenants.audit_records("alpha")
        self.assertEqual([e.event for e in events], ["report.created", "report.downloaded"])
        self.assertTrue(self.tenants.verify_audit_chain("alpha"))


if __name__ == "__main__":
    unittest.main()
