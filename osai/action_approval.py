"""Short-lived, payload-bound approval store for permissioned write actions.

The plaintext approval reference is returned once to the channel. Only a SHA-256
hash is persisted. Approval is bound to tenant, actor, action and the normalized
payload digest produced by ActionCoordinator.prepare().
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .actions import PreparedAction
from .contracts import ExecutionContext, JSONValue


class PendingApprovalError(RuntimeError):
    """Base class for sanitized approval-store failures."""


class PendingApprovalUnavailable(PendingApprovalError):
    """Approval is missing, expired, consumed or belongs to another actor."""


@dataclass(frozen=True)
class PendingApproval:
    approval_ref: str
    action: str
    payload: Mapping[str, JSONValue]
    payload_digest: str
    idempotency_key: str
    expires_at: int


class PendingApprovalStore:
    """SQLite-backed verifier implementing the F9 ApprovalVerifier contract."""

    def __init__(self, database_path: str | Path) -> None:
        self._db = sqlite3.connect(str(database_path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_action_approvals (
                approval_hash TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                action TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                claimed_at INTEGER,
                created_at INTEGER NOT NULL,
                UNIQUE(tenant_id, idempotency_key),
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE RESTRICT
            )
            """
        )
        self._db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pending_action_actor
            ON pending_action_approvals(tenant_id, actor_id, expires_at, claimed_at)
            """
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _hash(approval_ref: str) -> str:
        return hashlib.sha256(f"action-approval:{approval_ref}".encode()).hexdigest()

    @staticmethod
    def _now(now_epoch: int | None) -> int:
        value = int(time.time()) if now_epoch is None else now_epoch
        if value < 0:
            raise ValueError("now_epoch must be non-negative")
        return value

    def _purge_expired(self, now: int) -> None:
        self._db.execute(
            "DELETE FROM pending_action_approvals WHERE expires_at < ?",
            (now,),
        )
        self._db.commit()

    def issue(
        self,
        *,
        context: ExecutionContext,
        prepared: PreparedAction,
        request_payload: Mapping[str, JSONValue],
        ttl_seconds: int = 300,
        now_epoch: int | None = None,
    ) -> PendingApproval:
        if ttl_seconds < 30 or ttl_seconds > 900:
            raise ValueError("ttl_seconds must be between 30 and 900")
        now = self._now(now_epoch)
        self._purge_expired(now)
        approval_ref = f"tg:{secrets.token_urlsafe(24)}"
        idempotency_key = f"tg:{uuid.uuid4().hex}"
        payload_json = json.dumps(
            dict(request_payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        self._db.execute(
            """
            INSERT INTO pending_action_approvals(
                approval_hash,tenant_id,actor_id,action,payload_json,payload_digest,
                idempotency_key,expires_at,claimed_at,created_at
            ) VALUES(?,?,?,?,?,?,?,?,NULL,?)
            """,
            (
                self._hash(approval_ref),
                context.tenant_id,
                context.actor_id,
                prepared.action,
                payload_json,
                prepared.payload_digest,
                idempotency_key,
                now + ttl_seconds,
                now,
            ),
        )
        self._db.commit()
        return PendingApproval(
            approval_ref=approval_ref,
            action=prepared.action,
            payload=dict(request_payload),
            payload_digest=prepared.payload_digest,
            idempotency_key=idempotency_key,
            expires_at=now + ttl_seconds,
        )

    def resolve(
        self,
        *,
        context: ExecutionContext,
        approval_ref: str,
        now_epoch: int | None = None,
    ) -> PendingApproval:
        now = self._now(now_epoch)
        if not approval_ref or len(approval_ref) > 256:
            raise PendingApprovalUnavailable("approval unavailable")
        row = self._db.execute(
            """
            SELECT tenant_id,actor_id,action,payload_json,payload_digest,
                   idempotency_key,expires_at,claimed_at
            FROM pending_action_approvals
            WHERE approval_hash=?
            """,
            (self._hash(approval_ref),),
        ).fetchone()
        if (
            row is None
            or row["tenant_id"] != context.tenant_id
            or row["actor_id"] != context.actor_id
            or row["claimed_at"] is not None
            or int(row["expires_at"]) < now
        ):
            raise PendingApprovalUnavailable("approval unavailable")
        parsed = json.loads(str(row["payload_json"]))
        if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
            raise PendingApprovalUnavailable("approval unavailable")
        return PendingApproval(
            approval_ref=approval_ref,
            action=str(row["action"]),
            payload=parsed,
            payload_digest=str(row["payload_digest"]),
            idempotency_key=str(row["idempotency_key"]),
            expires_at=int(row["expires_at"]),
        )

    def verify(
        self,
        *,
        context: ExecutionContext,
        action: str,
        approval_ref: str,
        payload_digest: str,
    ) -> bool:
        now = self._now(None)
        try:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute(
                """
                SELECT tenant_id,actor_id,action,payload_digest,expires_at,claimed_at
                FROM pending_action_approvals
                WHERE approval_hash=?
                """,
                (self._hash(approval_ref),),
            ).fetchone()
            valid = (
                row is not None
                and row["tenant_id"] == context.tenant_id
                and row["actor_id"] == context.actor_id
                and row["action"] == action
                and row["payload_digest"] == payload_digest
                and row["claimed_at"] is None
                and int(row["expires_at"]) >= now
            )
            if not valid:
                self._db.rollback()
                return False
            changed = self._db.execute(
                """
                UPDATE pending_action_approvals
                SET claimed_at=?, payload_json='{}'
                WHERE approval_hash=? AND claimed_at IS NULL
                """,
                (now, self._hash(approval_ref)),
            ).rowcount
            if changed != 1:
                self._db.rollback()
                return False
            self._db.commit()
            return True
        except Exception:
            self._db.rollback()
            raise

    def cancel(
        self,
        *,
        context: ExecutionContext,
        approval_ref: str,
        now_epoch: int | None = None,
    ) -> None:
        now = self._now(now_epoch)
        changed = self._db.execute(
            """
            UPDATE pending_action_approvals SET claimed_at=?, payload_json='{}'
            WHERE approval_hash=? AND tenant_id=? AND actor_id=?
              AND claimed_at IS NULL AND expires_at>=?
            """,
            (
                now,
                self._hash(approval_ref),
                context.tenant_id,
                context.actor_id,
                now,
            ),
        ).rowcount
        self._db.commit()
        if changed != 1:
            raise PendingApprovalUnavailable("approval unavailable")
