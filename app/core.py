"""OS AI Core demo: typed, read-only capability dispatch over synthetic fixtures.

No external LLM, Google Drive, Telegram, database, or authentication integration.
"""
from __future__ import annotations
import csv
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import re
from threading import Lock
from typing import Any

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixture.json"


def amount(n: str | int | Decimal) -> str:
    return f"{Decimal(n):,.0f} AFN"


def normalized(s: str) -> str:
    return s.lower().translate(str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه"})).strip()


class CoreDemo:
    """An execution layer: intents map to allowlisted capabilities, never dynamic SQL."""

    def __init__(self, fixture: Path = FIXTURE):
        self.data = json.loads(fixture.read_text(encoding="utf-8"))
        self._events: list[dict[str, Any]] = []
        self._lock = Lock()
        self._calls = 0

    def status(self) -> dict[str, Any]:
        return {"mode": "DEMO_ONLY", "synthetic": True, "channels": ["Browser Telegram simulation"],
                "connectors": [{"name": "Google Drive / Excel", "mode": "SIMULATED"},
                               {"name": "Client REST API", "mode": "SIMULATED"}],
                "tenant": self.data["tenant"]}

    def sources(self) -> dict[str, Any]:
        return {"documents": [{"id": f["id"], "name": f["name"], "rows": len(f["rows"]), "source": f["source"]}
                              for f in self.data["drive_files"]],
                "projects": len(self.data["erp_projects"]), "accounts": len(self.data["erp_accounts"])}

    def audit(self, event: str, capability: str, result: str) -> None:
        with self._lock:
            self._calls += 1
            self._events.append({"id": self._calls, "time": datetime.now(timezone.utc).isoformat(),
                                 "event": event[:140], "capability": capability, "result": result})
            self._events = self._events[-50:]

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._events))

    def _sales(self) -> dict[str, Any]:
        f = self.data["drive_files"][0]
        total = sum((Decimal(row["amount_afn"]) for row in f["rows"]), Decimal("0"))
        return {"answer": f"مجموع فروشات ماه سپتمبر در معلومات آزمایشی {amount(total)} است. این مبلغ از {len(f['rows'])} رکورد محاسبه شده است.",
                "answer_en": f"Synthetic September sales total: {amount(total)} across {len(f['rows'])} records.",
                "capability": "drive.sales_total", "sources": [{"id": f["id"], "name": f["name"], "rows": len(f["rows"])}],
                "figures": {"sales_afn": str(total)}, "report": "/api/report/sales.csv"}

    def _expenses(self) -> dict[str, Any]:
        f = self.data["drive_files"][1]
        total = sum((Decimal(row["amount_afn"]) for row in f["rows"]), Decimal("0"))
        return {"answer": f"مجموع مصارف آزمایشی سپتمبر {amount(total)} در {len(f['rows'])} رکورد است.",
                "answer_en": f"Synthetic September expenses: {amount(total)}.",
                "capability": "drive.expenses_total", "sources": [{"id": f["id"], "name": f["name"], "rows": len(f["rows"])}],
                "figures": {"expenses_afn": str(total)}, "report": "/api/report/expenses.csv"}

    def _projects(self) -> dict[str, Any]:
        projects = self.data["erp_projects"]
        budget = sum((Decimal(p["budget_afn"]) for p in projects), Decimal(0))
        spent = sum((Decimal(p["spent_afn"]) for p in projects), Decimal(0))
        lines = "; ".join(f"{p['name']}: {amount(p['spent_afn'])} از {amount(p['budget_afn'])}" for p in projects)
        return {"answer": f"پروژه‌های آزمایشی: {lines}. مجموع مصرف {amount(spent)} از بودجهٔ {amount(budget)} است.",
                "answer_en": f"Demo projects: total spent {amount(spent)} of budget {amount(budget)}.",
                "capability": "erp.projects_summary", "sources": [{"id": "erp-projects", "name": "Simulated Client REST API / projects", "rows": len(projects)}],
                "figures": {"budget_afn": str(budget), "spent_afn": str(spent)}, "report": "/api/report/projects.csv"}

    def _accounts(self) -> dict[str, Any]:
        accounts = self.data["erp_accounts"]
        lines = "; ".join(f"{a['code']} {a['name']}: debit {amount(a['debit_afn'])}, credit {amount(a['credit_afn'])}" for a in accounts)
        return {"answer": f"معلومات نمونهٔ حساب‌ها: {lines}. این یک لیست نمایشی است، نه تراز آزمایشی کامل.",
                "answer_en": f"Demo accounts: {lines}. This is not a complete trial balance.",
                "capability": "erp.accounts_view", "sources": [{"id": "erp-accounts", "name": "Simulated Client REST API / accounts", "rows": len(accounts)}],
                "figures": {}, "report": "/api/report/accounts.csv"}

    def ask(self, message: str, *, actor: str = "demo-admin", tenant: str = "demo-company") -> dict[str, Any]:
        if not isinstance(message, str) or not message.strip() or len(message) > 1200:
            raise ValueError("Message must contain 1 to 1200 characters")
        if tenant != self.data["tenant"]:
            self.audit(message, "tenant.guard", "DENIED")
            raise PermissionError("Demo tenant not found")
        q = normalized(message)
        # Demo read-only operation guard: no financial or data mutations.
        mutations = ["تغییر", "اضافه کن", "حذف", "ویرایش", "ثبت کن", "پرداخت", "انتقال", "modify", "delete", "update", "transfer", "pay ", "create ", "post journal"]
        if any(m in q for m in mutations):
            self.audit(message, "write.guard", "DENIED")
            return {"answer": "این دیمو فقط خواندنی است. تغییرات مالی و اسناد باید در نسخهٔ عملیاتی با مجوز، تأیید و قواعد حسابداری انجام شوند.",
                    "answer_en": "Read-only demo: write operations are disabled.", "capability": "write.guard", "sources": [], "figures": {}, "report": None, "denied": True}
        if any(t in q for t in ["پروژه", "ساختم", "بودجه", "construction", "project", "budget"]):
            result = self._projects()
        elif any(t in q for t in ["فروش", "عواید", "sales", "revenue", "income"]):
            result = self._sales()
        elif any(t in q for t in ["مصارف", "هزینه", "expenses", "cost"]):
            result = self._expenses()
        elif any(t in q for t in ["حساب", "اکانت", "accounts", "ledger", "balance"]):
            result = self._accounts()
        else:
            result = {"answer": "در این دیمو می‌توانی دربارهٔ فروشات، مصارف، پروژه‌ها یا حساب‌های نمونه سؤال کنی. اتصال واقعی به Google Drive، تلگرام و سیستم مشتری هنوز فعال نیست.",
                      "answer_en": "Try sales, expenses, projects, or accounts. This demo uses fixtures only.",
                      "capability": "help", "sources": [], "figures": {}, "report": None}
        self.audit(message, result["capability"], "OK")
        return {**result, "demo": True, "actor": actor, "tenant": tenant}

    def report(self, kind: str) -> str:
        table: list[dict[str, Any]]
        if kind == "sales": table = self.data["drive_files"][0]["rows"]
        elif kind == "expenses": table = self.data["drive_files"][1]["rows"]
        elif kind == "projects": table = self.data["erp_projects"]
        elif kind == "accounts": table = self.data["erp_accounts"]
        else: raise ValueError("Unknown report")
        out = StringIO()
        out.write("\ufeff")  # Excel-compatible UTF-8 CSV
        writer = csv.DictWriter(out, fieldnames=list(table[0].keys()))
        writer.writeheader()
        writer.writerows(table)
        self.audit(f"download:{kind}", f"report.{kind}", "OK")
        return out.getvalue()
