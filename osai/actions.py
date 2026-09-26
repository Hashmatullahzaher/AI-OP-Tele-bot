"""Permissioned write/action orchestration for OS AI Core F9.

The model/channel never performs a mutation directly. A trusted server context,
an explicit action capability grant, a step-up approval, a deterministic domain
validator, a source adapter that honors idempotency, and a durable action journal
must all succeed before a mutation can be disclosed as complete.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from .contracts import (
    CapabilityManifest,
    ExecutionContext,
    JSONValue,
    PolicyDenied,
    SchemaValidationError,
    validate_value,
)
from .storage import TenantSecurityStore


class ActionError(RuntimeError):
    """Base class for sanitized action failures."""


class SourceOutcomeUncertain(ActionError):
    """The source may have committed; reconciliation is required before retry."""

    outcome_uncertain = True


class ActionNotAllowed(ActionError):
    """Action or payload is outside the registered F9 contract."""


class ApprovalRequired(ActionError):
    """A valid out-of-band/step-up approval was not presented."""


class IdempotencyConflict(ActionError):
    """An idempotency key was reused for a different request."""


class ActionInProgress(ActionError):
    """A prior source call may still be in progress and needs reconciliation."""


class FinancialInvariantError(ActionError):
    """A draft financial request violates deterministic accounting invariants."""


@dataclass(frozen=True)
class ActionReceipt:
    action: str
    source_record_id: str
    source_state: str
    source_tenant_id: str
    source_actor_id: str
    idempotency_key: str
    revision: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "action": self.action,
            "source_record_id": self.source_record_id,
            "source_state": self.source_state,
            "source_tenant_id": self.source_tenant_id,
            "source_actor_id": self.source_actor_id,
            "idempotency_key": self.idempotency_key,
            "revision": self.revision,
        }


class ApprovalVerifier(Protocol):
    def verify(
        self,
        *,
        context: ExecutionContext,
        action: str,
        approval_ref: str,
        payload_digest: str,
    ) -> bool:
        """Verify a short-lived approval outside model-controlled content."""


class SourceWriteAdapter(Protocol):
    def execute(
        self,
        *,
        action: str,
        payload: Mapping[str, JSONValue],
        context: ExecutionContext,
        idempotency_key: str,
    ) -> ActionReceipt:
        """Execute one already-authorized, validated source-domain mutation."""


@dataclass(frozen=True)
class StaticApprovalVerifier:
    """Test-only verifier. Production must use a real step-up approval service."""

    approved_refs: frozenset[str]

    def verify(
        self,
        *,
        context: ExecutionContext,
        action: str,
        approval_ref: str,
        payload_digest: str,
    ) -> bool:
        return approval_ref in self.approved_refs


@dataclass(frozen=True)
class ActionDefinition:
    action: str
    capability: str
    source_alias: str
    input_schema: Mapping[str, Any]
    required_source_states: tuple[str, ...]

    def manifest(self) -> CapabilityManifest:
        return CapabilityManifest.from_dict(
            {
                "schema_version": "0.1",
                "connector_type": "action",
                "capability": self.capability,
                "action": "WRITE",
                "input_schema": self.input_schema,
                "output_schema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
                "required_scopes": ["write:approved"],
                "resource_allowlist_ref": f"policyref:{self.source_alias}",
                "mutates": True,
                "timeout_seconds": 30,
            }
        )


def _str_schema(*, max_length: int = 256) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": max_length}


CUSTOMER_CREATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": _str_schema(max_length=200),
        "customer_type": {"type": "string", "enum": ["INDIVIDUAL", "COMPANY"]},
        "phone": {"type": "string", "maxLength": 64},
        "email": {"type": "string", "maxLength": 254},
        "address": {"type": "string", "maxLength": 500},
    },
    "required": ["name", "customer_type"],
    "additionalProperties": False,
}

CUSTOMER_UPDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "customer_id": _str_schema(max_length=128),
        "changes": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "maxLength": 200},
                "phone": {"type": "string", "maxLength": 64},
                "email": {"type": "string", "maxLength": 254},
                "address": {"type": "string", "maxLength": 500},
                "status": {"type": "string", "enum": ["ACTIVE", "INACTIVE"]},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "required": ["customer_id", "changes"],
    "additionalProperties": False,
}

PROCUREMENT_CREATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requester": _str_schema(max_length=128),
        "department": _str_schema(max_length=128),
        "currency": _str_schema(max_length=8),
        "justification": {"type": "string", "maxLength": 1000},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": _str_schema(max_length=300),
                    "quantity": _str_schema(max_length=32),
                    "unit_estimated_cost": _str_schema(max_length=64),
                },
                "required": ["description", "quantity", "unit_estimated_cost"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["requester", "department", "currency", "items"],
    "additionalProperties": False,
}

DRAFT_VOUCHER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "voucher_date": _str_schema(max_length=10),
        "currency": _str_schema(max_length=8),
        "description": _str_schema(max_length=500),
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "account_code": _str_schema(max_length=64),
                    "debit": _str_schema(max_length=64),
                    "credit": _str_schema(max_length=64),
                    "memo": {"type": "string", "maxLength": 300},
                },
                "required": ["account_code", "debit", "credit"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["voucher_date", "currency", "description", "entries"],
    "additionalProperties": False,
}


def default_action_definitions(source_alias: str) -> dict[str, ActionDefinition]:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", source_alias):
        raise ValueError("invalid source alias")
    definitions = (
        ActionDefinition(
            "customer.create",
            "customer.create",
            source_alias,
            CUSTOMER_CREATE_SCHEMA,
            ("CREATED", "ACTIVE"),
        ),
        ActionDefinition(
            "customer.update",
            "customer.update",
            source_alias,
            CUSTOMER_UPDATE_SCHEMA,
            ("UPDATED", "ACTIVE", "INACTIVE"),
        ),
        ActionDefinition(
            "procurement.request.create",
            "procurement.request.create",
            source_alias,
            PROCUREMENT_CREATE_SCHEMA,
            ("DRAFT", "SUBMITTED", "CREATED"),
        ),
        ActionDefinition(
            "finance.draft_voucher.create",
            "finance.draft_voucher.create",
            source_alias,
            DRAFT_VOUCHER_SCHEMA,
            ("DRAFT",),
        ),
    )
    return {item.action: item for item in definitions}


class ActionJournal:
    """Durable local idempotency journal; source endpoint must also honor the key."""

    def __init__(self, database_path: str | Path) -> None:
        self._db = sqlite3.connect(str(database_path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS action_requests (
                tenant_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                action TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('IN_PROGRESS','COMPLETED','FAILED')),
                receipt_json TEXT,
                error_code TEXT,
                PRIMARY KEY (tenant_id, idempotency_key),
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE RESTRICT
            )
            """
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def reserve(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        action: str,
        idempotency_key: str,
        payload_digest: str,
    ) -> ActionReceipt | None:
        try:
            self._db.execute("BEGIN IMMEDIATE")
            pending = self._db.execute(
                """
                SELECT idempotency_key FROM action_requests
                WHERE tenant_id=? AND actor_id=? AND action=? AND payload_digest=?
                  AND status='IN_PROGRESS'
                LIMIT 1
                """,
                (tenant_id, actor_id, action, payload_digest),
            ).fetchone()
            if pending is not None and str(pending["idempotency_key"]) != idempotency_key:
                raise ActionInProgress("matching action is pending reconciliation")
            row = self._db.execute(
                "SELECT * FROM action_requests WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, idempotency_key),
            ).fetchone()
            if row is None:
                self._db.execute(
                    """
                    INSERT INTO action_requests(
                        tenant_id,idempotency_key,action,actor_id,payload_digest,status
                    ) VALUES(?,?,?,?,?,'IN_PROGRESS')
                    """,
                    (tenant_id, idempotency_key, action, actor_id, payload_digest),
                )
                self._db.commit()
                return None
            if (
                row["action"] != action
                or row["actor_id"] != actor_id
                or row["payload_digest"] != payload_digest
            ):
                raise IdempotencyConflict("idempotency key conflicts with an earlier request")
            if row["status"] == "IN_PROGRESS":
                raise ActionInProgress("action is pending reconciliation")
            if row["status"] == "FAILED":
                raise ActionError("earlier action attempt failed; use a new idempotency key after review")
            receipt_raw = row["receipt_json"]
            if not isinstance(receipt_raw, str):
                raise ActionError("completed action receipt is unavailable")
            data = json.loads(receipt_raw)
            receipt = ActionReceipt(**data)
            self._db.commit()
            return receipt
        except Exception:
            self._db.rollback()
            raise

    def complete(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        receipt: ActionReceipt,
    ) -> None:
        changed = self._db.execute(
            """
            UPDATE action_requests SET status='COMPLETED', receipt_json=?, error_code=NULL
            WHERE tenant_id=? AND idempotency_key=? AND status='IN_PROGRESS'
            """,
            (
                json.dumps(receipt.as_dict(), sort_keys=True, separators=(",", ":")),
                tenant_id,
                idempotency_key,
            ),
        ).rowcount
        self._db.commit()
        if changed != 1:
            raise ActionError("action journal completion failed")

    def fail(self, *, tenant_id: str, idempotency_key: str, error_code: str) -> None:
        self._db.execute(
            """
            UPDATE action_requests SET status='FAILED', error_code=?
            WHERE tenant_id=? AND idempotency_key=? AND status='IN_PROGRESS'
            """,
            (error_code[:64], tenant_id, idempotency_key),
        )
        self._db.commit()


class ActionCoordinator:
    """Authorize -> validate -> approve -> reserve -> source mutate -> audit."""

    _IDEMPOTENCY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")
    _APPROVAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,255}")

    def __init__(
        self,
        *,
        security_store: TenantSecurityStore,
        journal: ActionJournal,
        definitions: Mapping[str, ActionDefinition],
        adapter: SourceWriteAdapter,
        approval_verifier: ApprovalVerifier,
    ) -> None:
        self.security_store = security_store
        self.journal = journal
        self.definitions = dict(definitions)
        self.adapter = adapter
        self.approval_verifier = approval_verifier

    def execute(
        self,
        *,
        context: ExecutionContext,
        action: str,
        payload: Mapping[str, JSONValue],
        approval_ref: str,
        idempotency_key: str,
    ) -> ActionReceipt:
        definition = self.definitions.get(action)
        if definition is None:
            raise ActionNotAllowed("action is not registered")
        if self._IDEMPOTENCY_RE.fullmatch(idempotency_key) is None:
            raise ActionNotAllowed("invalid idempotency key")
        if self._APPROVAL_RE.fullmatch(approval_ref) is None:
            raise ApprovalRequired("approval is required")
        self.security_store.assert_actor(context)
        self.security_store.authorize(context, definition.manifest())
        try:
            validate_value(definition.input_schema, payload)
        except SchemaValidationError as exc:
            raise ActionNotAllowed("action payload is invalid") from exc
        normalized = _validate_domain(action, payload)
        payload_digest = _digest(action, normalized)
        if not self.approval_verifier.verify(
            context=context,
            action=action,
            approval_ref=approval_ref,
            payload_digest=payload_digest,
        ):
            self.security_store.append_audit(
                context=context,
                event="action.approval_denied",
                capability=definition.capability,
                resource_scope=f"client_api:{definition.source_alias}",
                result="DENIED",
                sensitive_payload={"payload_digest": payload_digest},
            )
            raise ApprovalRequired("approval is invalid or expired")

        prior = self.journal.reserve(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            action=action,
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
        )
        if prior is not None:
            self.security_store.append_audit(
                context=context,
                event="action.idempotent_replay",
                capability=definition.capability,
                resource_scope=f"client_api:{definition.source_alias}",
                result="OK",
                sensitive_payload={"payload_digest": payload_digest},
            )
            return prior

        self.security_store.append_audit(
            context=context,
            event="action.requested",
            capability=definition.capability,
            resource_scope=f"client_api:{definition.source_alias}",
            result="APPROVED",
            sensitive_payload={"payload_digest": payload_digest},
        )
        try:
            receipt = self.adapter.execute(
                action=action,
                payload=normalized,
                context=context,
                idempotency_key=idempotency_key,
            )
            try:
                _validate_receipt(
                    receipt,
                    definition=definition,
                    context=context,
                    idempotency_key=idempotency_key,
                )
            except Exception as exc:
                raise SourceOutcomeUncertain(
                    "source receipt requires reconciliation"
                ) from exc
        except Exception as exc:
            if getattr(exc, "outcome_uncertain", False):
                self.security_store.append_audit(
                    context=context,
                    event="action.reconciliation_required",
                    capability=definition.capability,
                    resource_scope=f"client_api:{definition.source_alias}",
                    result="PENDING",
                    sensitive_payload={"payload_digest": payload_digest},
                )
                raise ActionInProgress(
                    "source outcome is uncertain; reconciliation is required"
                ) from exc
            self.journal.fail(
                tenant_id=context.tenant_id,
                idempotency_key=idempotency_key,
                error_code=type(exc).__name__,
            )
            self.security_store.append_audit(
                context=context,
                event="action.failed",
                capability=definition.capability,
                resource_scope=f"client_api:{definition.source_alias}",
                result="FAILED",
                sensitive_payload={"payload_digest": payload_digest},
            )
            raise
        self.journal.complete(
            tenant_id=context.tenant_id,
            idempotency_key=idempotency_key,
            receipt=receipt,
        )
        self.security_store.append_audit(
            context=context,
            event="action.completed",
            capability=definition.capability,
            resource_scope=f"client_api:{definition.source_alias}",
            result="OK",
            sensitive_payload={
                "payload_digest": payload_digest,
                "source_record_id": receipt.source_record_id,
            },
        )
        return receipt


def _digest(action: str, payload: Mapping[str, JSONValue]) -> str:
    material = json.dumps(
        {"action": action, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


_MONEY_RE = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?")


def _decimal(value: object, *, field: str, allow_zero: bool = True) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FinancialInvariantError(f"{field} is not a valid decimal") from exc
    if not parsed.is_finite():
        raise FinancialInvariantError(f"{field} must be finite")
    if parsed < 0 or (not allow_zero and parsed == 0):
        raise FinancialInvariantError(f"{field} must be positive")
    return parsed.quantize(Decimal("0.01"))


def _money_decimal(value: object, *, field: str) -> Decimal:
    text = str(value)
    if _MONEY_RE.fullmatch(text) is None:
        raise FinancialInvariantError(
            f"{field} must be a non-negative decimal with at most two fractional digits"
        )
    return _decimal(text, field=field)


def _validate_domain(action: str, payload: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    normalized = dict(payload)
    if action == "customer.update":
        changes = payload.get("changes")
        if not isinstance(changes, Mapping) or not changes:
            raise ActionNotAllowed("customer update requires at least one allowed change")
        if "customer_id" in changes:
            raise ActionNotAllowed("customer identity is immutable")
    elif action == "procurement.request.create":
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise ActionNotAllowed("procurement request requires at least one item")
        total = Decimal("0.00")
        for item in items:
            if not isinstance(item, Mapping):
                raise ActionNotAllowed("procurement item is invalid")
            quantity = _decimal(item.get("quantity"), field="quantity", allow_zero=False)
            unit_cost = _decimal(item.get("unit_estimated_cost"), field="unit_estimated_cost")
            total += quantity * unit_cost
        normalized["estimated_total"] = format(total.quantize(Decimal("0.01")), "f")
    elif action == "finance.draft_voucher.create":
        entries = payload.get("entries")
        if not isinstance(entries, list) or len(entries) < 2:
            raise FinancialInvariantError("draft voucher requires at least two entries")
        total_debit = Decimal("0.00")
        total_credit = Decimal("0.00")
        normalized_entries: list[JSONValue] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise FinancialInvariantError("voucher entry is invalid")
            debit = _money_decimal(entry.get("debit"), field="debit")
            credit = _money_decimal(entry.get("credit"), field="credit")
            if debit > 0 and credit > 0:
                raise FinancialInvariantError("an entry cannot contain both debit and credit")
            if debit == 0 and credit == 0:
                raise FinancialInvariantError("an entry must contain debit or credit")
            total_debit += debit
            total_credit += credit
            normalized_entry = dict(entry)
            normalized_entry["debit"] = format(debit, ".2f")
            normalized_entry["credit"] = format(credit, ".2f")
            normalized_entries.append(normalized_entry)
        if total_debit != total_credit or total_debit == 0:
            raise FinancialInvariantError("draft voucher must balance debit and credit")
        normalized["entries"] = normalized_entries
        normalized["total_debit"] = format(total_debit, ".2f")
        normalized["total_credit"] = format(total_credit, ".2f")
        normalized["status"] = "DRAFT"
    return normalized


def _validate_receipt(
    receipt: ActionReceipt,
    *,
    definition: ActionDefinition,
    context: ExecutionContext,
    idempotency_key: str,
) -> None:
    if receipt.action != definition.action:
        raise ActionError("source receipt action mismatch")
    if receipt.source_tenant_id != context.tenant_id:
        raise PolicyDenied("source did not confirm tenant authorization")
    if receipt.source_actor_id != context.actor_id:
        raise PolicyDenied("source did not confirm actor authorization")
    if receipt.idempotency_key != idempotency_key:
        raise ActionError("source did not confirm idempotency key")
    if not receipt.source_record_id.strip():
        raise ActionError("source receipt is missing record id")
    if definition.action == "finance.draft_voucher.create" and receipt.source_state != "DRAFT":
        raise FinancialInvariantError("voucher source state must remain DRAFT")
    if receipt.source_state not in definition.required_source_states:
        raise ActionError("source returned a disallowed state")
