"""Command-line Excel sandbox UAT for OS AI Core."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .actions import ActionCoordinator, ActionJournal, StaticApprovalVerifier, default_action_definitions
from .connectors.local_excel import ExcelSandboxWriteAdapter, initialize_excel_sandbox
from .contracts import ExecutionContext
from .storage import TenantSecurityStore

SOURCE = "excel-pilot"
TENANT = "excel-uat"
ACTOR = "owner"
APPROVAL = "approval:excel-uat:12345678"


def _absolute(value: str) -> Path:
    return Path(value).expanduser().resolve()


def init_workbook(path: Path) -> None:
    initialize_excel_sandbox(path)
    print(json.dumps({"ok": True, "workbook": str(path)}, ensure_ascii=False))


def smoke(path: Path, db: Path) -> None:
    initialize_excel_sandbox(path)
    if db.exists():
        db.unlink()

    store = TenantSecurityStore(db)
    journal = ActionJournal(db)
    try:
        store.add_tenant(TENANT)
        store.add_actor(TENANT, ACTOR, role="TENANT_ADMIN")
        for capability in (
            "customer.create",
            "customer.update",
            "procurement.request.create",
            "finance.draft_voucher.create",
        ):
            store.grant(TENANT, ACTOR, capability, f"client_api:{SOURCE}")

        context = ExecutionContext.issue(
            tenant_id=TENANT,
            actor_id=ACTOR,
            resource_scope=(f"client_api:{SOURCE}",),
            session_assurance="excel-uat-step-up",
        )
        adapter = ExcelSandboxWriteAdapter(workbook_path=path, source_alias=SOURCE)
        coordinator = ActionCoordinator(
            security_store=store,
            journal=journal,
            definitions=default_action_definitions(SOURCE),
            adapter=adapter,
            approval_verifier=StaticApprovalVerifier(frozenset({APPROVAL})),
        )

        def execute(action: str, payload: dict, key: str):
            return coordinator.execute(
                context=context,
                action=action,
                payload=payload,
                approval_ref=APPROVAL,
                idempotency_key=key,
            )

        customer = execute(
            "customer.create",
            {
                "name": "Aryana Trading",
                "customer_type": "COMPANY",
                "phone": "0700000000",
                "address": "Mazar-e-Sharif",
            },
            "idem:excel-smoke:cust:001",
        )
        updated = execute(
            "customer.update",
            {
                "customer_id": customer.source_record_id,
                "changes": {"phone": "0799999999"},
            },
            "idem:excel-smoke:cust:002",
        )
        procurement = execute(
            "procurement.request.create",
            {
                "requester": "Hash",
                "department": "Admin",
                "currency": "AFN",
                "justification": "Excel UAT office supplies",
                "items": [
                    {"description": "Paper", "quantity": "2", "unit_estimated_cost": "1250.50"},
                    {"description": "Toner", "quantity": "1", "unit_estimated_cost": "5000"},
                ],
            },
            "idem:excel-smoke:pr:001",
        )
        voucher = execute(
            "finance.draft_voucher.create",
            {
                "voucher_date": "2026-09-26",
                "currency": "AFN",
                "description": "Excel UAT expense",
                "entries": [
                    {"account_code": "6300", "debit": "9000", "credit": "0", "memo": "Expense"},
                    {"account_code": "1100", "debit": "0", "credit": "9000", "memo": "Cash"},
                ],
            },
            "idem:excel-smoke:dv:001",
        )

        print(
            json.dumps(
                {
                    "ok": True,
                    "workbook": str(path),
                    "database": str(db),
                    "customer": customer.as_dict(),
                    "customer_update": updated.as_dict(),
                    "procurement": procurement.as_dict(),
                    "draft_voucher": voucher.as_dict(),
                    "audit_chain_ok": store.verify_audit_chain(TENANT),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        journal.close()
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="OS AI Core Excel sandbox UAT")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="create an empty sandbox workbook")
    init_parser.add_argument("--path", required=True)

    smoke_parser = sub.add_parser("smoke", help="run the four F9 actions against a fresh workbook")
    smoke_parser.add_argument("--path", required=True)
    smoke_parser.add_argument("--db", required=True)

    args = parser.parse_args()
    if args.command == "init":
        init_workbook(_absolute(args.path))
    else:
        smoke(_absolute(args.path), _absolute(args.db))


if __name__ == "__main__":
    main()
