import csv
import json
import unittest
from io import StringIO

from app.core import CoreDemo


class CoreDemoTests(unittest.TestCase):
    def setUp(self):
        self.core = CoreDemo()

    def test_sales_grounded_total(self):
        r = self.core.ask("مجموع فروشات سپتمبر")
        self.assertEqual(r["figures"]["sales_afn"], "44000")
        self.assertEqual(r["sources"][0]["id"], "drive-sales-sep")
        self.assertEqual(r["sources"][0]["rows"], 4)
        self.assertEqual(r["report"], "/api/report/sales.csv")

    def test_expense_grounded_total(self):
        r = self.core.ask("مصارف ماه سپتمبر")
        self.assertEqual(r["figures"]["expenses_afn"], "12600")
        self.assertEqual(r["capability"], "drive.expenses_total")

    def test_project_query_prioritized_over_expense_word(self):
        r = self.core.ask("بودجه و مصارف پروژه‌ها را نشان بده")
        self.assertEqual(r["figures"]["spent_afn"], "487500")
        self.assertEqual(r["figures"]["budget_afn"], "1250000")
        self.assertEqual(r["capability"], "erp.projects_summary")

    def test_accounts_not_presented_as_trial_balance(self):
        r = self.core.ask("معلومات حساب‌ها")
        self.assertEqual(r["capability"], "erp.accounts_view")
        self.assertIn("نه تراز آزمایشی کامل", r["answer"])

    def test_mutation_denied_and_fixture_unchanged(self):
        original = json.dumps(self.core.data, sort_keys=True)
        r = self.core.ask("پرداخت را ثبت کن")
        self.assertTrue(r["denied"])
        self.assertEqual(self.core.events()[0]["result"], "DENIED")
        self.assertEqual(json.dumps(self.core.data, sort_keys=True), original)

    def test_tenant_mismatch_denied(self):
        with self.assertRaises(PermissionError):
            self.core.ask("مجموع فروشات", tenant="another-company")
        self.assertEqual(self.core.events()[0]["result"], "DENIED")

    def test_csv_excel_compatible(self):
        report = self.core.report("sales")
        self.assertTrue(report.startswith("\ufeff"))
        rows = list(csv.DictReader(StringIO(report.lstrip("\ufeff"))))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["amount_afn"], "12500")

    def test_no_unknown_report(self):
        with self.assertRaises(ValueError):
            self.core.report("secret")

    def test_message_validation(self):
        for bad in ["", " " * 8, "a" * 1201]:
            with self.assertRaises(ValueError):
                self.core.ask(bad)

    def test_help_does_not_hallucinate(self):
        r = self.core.ask("چه خبر؟")
        self.assertEqual(r["capability"], "help")
        self.assertEqual(r["sources"], [])


if __name__ == "__main__":
    unittest.main()
