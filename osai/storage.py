"""Tenant-isolated SQLite persistence for identity, policy, refs, and audit.

This is an F1 foundation, not a complete production identity provider.  It stores
only secret *references*, never secret values, and uses compound tenant keys.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import CapabilityManifest, ExecutionContext, PolicyDenied


@dataclass(frozen=True)
class AuditRecord:
    sequence: int
    tenant_id: str
    actor_id: str
    event: str
    capability: str | None
    resource_scope: str | None
    correlation_id: str
    result: str
    created_at: str
    event_hash: str


@dataclass(frozen=True)
class TelegramBinding:
    bot_alias: str
    chat_id: int
    telegram_user_id: int
    tenant_id: str
    actor_id: str
    status: str


class TenantSecurityStore:
    """Small persistence boundary with explicit tenant ownership on every lookup."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> TenantSecurityStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _init_schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK(status IN ('ACTIVE','DISABLED'))
            );
            CREATE TABLE IF NOT EXISTS actors (
                tenant_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('ACTIVE','REVOKED')),
                PRIMARY KEY (tenant_id, actor_id),
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS grants (
                tenant_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                capability TEXT NOT NULL,
                resource_scope TEXT NOT NULL,
                PRIMARY KEY (tenant_id, actor_id, capability, resource_scope),
                FOREIGN KEY (tenant_id, actor_id) REFERENCES actors(tenant_id, actor_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS connectors (
                tenant_id TEXT NOT NULL,
                connector_id TEXT NOT NULL,
                connector_type TEXT NOT NULL,
                credential_ref TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('ACTIVE','DISABLED')),
                PRIMARY KEY (tenant_id, connector_id),
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS scoped_objects (
                tenant_id TEXT NOT NULL,
                object_kind TEXT NOT NULL CHECK(object_kind IN ('cache','report')),
                object_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (tenant_id, object_kind, object_id),
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                event TEXT NOT NULL,
                capability TEXT,
                resource_scope TEXT,
                correlation_id TEXT NOT NULL,
                result TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                created_at TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_audit_tenant_seq
            ON audit_events(tenant_id, sequence);

            CREATE TABLE IF NOT EXISTS telegram_pairing_challenges (
                challenge_hash TEXT PRIMARY KEY,
                bot_alias TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                used_at INTEGER,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (tenant_id, actor_id)
                    REFERENCES actors(tenant_id, actor_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_telegram_pairing_actor
            ON telegram_pairing_challenges(tenant_id, actor_id, bot_alias);

            CREATE TABLE IF NOT EXISTS telegram_bindings (
                bot_alias TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                telegram_user_id INTEGER NOT NULL,
                tenant_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('ACTIVE','REVOKED')),
                paired_at INTEGER NOT NULL,
                PRIMARY KEY (bot_alias, chat_id),
                FOREIGN KEY (tenant_id, actor_id)
                    REFERENCES actors(tenant_id, actor_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_telegram_binding_actor
            ON telegram_bindings(tenant_id, actor_id, status);

            CREATE TABLE IF NOT EXISTS telegram_updates (
                bot_alias TEXT NOT NULL,
                update_id INTEGER NOT NULL,
                received_at INTEGER NOT NULL,
                PRIMARY KEY (bot_alias, update_id)
            );
            """
        )
        self._db.commit()

    def add_tenant(self, tenant_id: str) -> None:
        self._db.execute(
            "INSERT INTO tenants(tenant_id,status) VALUES(?, 'ACTIVE')",
            (tenant_id,),
        )
        self._db.commit()

    def add_actor(self, tenant_id: str, actor_id: str) -> None:
        self._require_active_tenant(tenant_id)
        self._db.execute(
            "INSERT INTO actors(tenant_id,actor_id,status) VALUES(?, ?, 'ACTIVE')",
            (tenant_id, actor_id),
        )
        self._db.commit()

    def revoke_actor(self, tenant_id: str, actor_id: str) -> None:
        changed = self._db.execute(
            "UPDATE actors SET status='REVOKED' WHERE tenant_id=? AND actor_id=?",
            (tenant_id, actor_id),
        ).rowcount
        self._db.commit()
        if changed != 1:
            raise PolicyDenied("actor unavailable")

    def grant(self, tenant_id: str, actor_id: str, capability: str, resource_scope: str) -> None:
        self._require_active_actor(tenant_id, actor_id)
        self._db.execute(
            "INSERT INTO grants(tenant_id,actor_id,capability,resource_scope) VALUES(?,?,?,?)",
            (tenant_id, actor_id, capability, resource_scope),
        )
        self._db.commit()

    def authorize(self, context: ExecutionContext, manifest: CapabilityManifest) -> None:
        self._require_active_actor(context.tenant_id, context.actor_id)
        requested = set(context.resource_scope) or {"*"}
        rows = self._db.execute(
            "SELECT resource_scope FROM grants WHERE tenant_id=? AND actor_id=? AND capability=?",
            (context.tenant_id, context.actor_id, manifest.capability),
        ).fetchall()
        granted = {str(r["resource_scope"]) for r in rows}
        if not granted or not all(scope in granted or "*" in granted for scope in requested):
            self.append_audit(
                context=context,
                event="policy.deny",
                capability=manifest.capability,
                resource_scope=",".join(sorted(requested)),
                result="DENIED",
            )
            raise PolicyDenied("capability or resource scope denied")

    def register_connector(
        self,
        *,
        tenant_id: str,
        connector_id: str,
        connector_type: str,
        credential_ref: str,
    ) -> None:
        self._require_active_tenant(tenant_id)
        if not credential_ref.startswith("secretref:"):
            raise ValueError("credential_ref must be an opaque secretref:, not a secret value")
        self._db.execute(
            "INSERT INTO connectors(tenant_id,connector_id,connector_type,credential_ref,state) "
            "VALUES(?,?,?,?, 'ACTIVE')",
            (tenant_id, connector_id, connector_type, credential_ref),
        )
        self._db.commit()

    def credential_ref(
        self,
        *,
        context: ExecutionContext,
        owner_tenant_id: str,
        connector_id: str,
    ) -> str:
        self._same_tenant(context, owner_tenant_id)
        self._require_active_actor(context.tenant_id, context.actor_id)
        allowed = self._db.execute(
            "SELECT 1 FROM grants WHERE tenant_id=? AND actor_id=? AND capability=? "
            "AND resource_scope IN (?, '*') LIMIT 1",
            (
                context.tenant_id,
                context.actor_id,
                "connector.credential_ref.read",
                f"connector:{connector_id}",
            ),
        ).fetchone()
        if allowed is None:
            raise PolicyDenied("connector unavailable")
        row = self._db.execute(
            "SELECT credential_ref,state FROM connectors WHERE tenant_id=? AND connector_id=?",
            (context.tenant_id, connector_id),
        ).fetchone()
        if row is None or row["state"] != "ACTIVE":
            raise PolicyDenied("connector unavailable")
        return str(row["credential_ref"])

    def put_scoped(self, *, tenant_id: str, kind: str, object_id: str, payload: Mapping[str, Any]) -> None:
        self._require_active_tenant(tenant_id)
        self._db.execute(
            "INSERT OR REPLACE INTO scoped_objects(tenant_id,object_kind,object_id,payload_json) VALUES(?,?,?,?)",
            (tenant_id, kind, object_id, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
        )
        self._db.commit()

    def get_scoped(
        self,
        *,
        context: ExecutionContext,
        owner_tenant_id: str,
        kind: str,
        object_id: str,
    ) -> dict[str, Any]:
        self._same_tenant(context, owner_tenant_id)
        self._require_active_actor(context.tenant_id, context.actor_id)
        row = self._db.execute(
            "SELECT payload_json FROM scoped_objects WHERE tenant_id=? AND object_kind=? AND object_id=?",
            (context.tenant_id, kind, object_id),
        ).fetchone()
        if row is None:
            raise PolicyDenied(f"{kind} unavailable")
        parsed = json.loads(str(row["payload_json"]))
        if not isinstance(parsed, dict):
            raise RuntimeError("stored scoped object is invalid")
        return parsed

    @staticmethod
    def _validate_bot_alias(bot_alias: str) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", bot_alias):
            raise ValueError("invalid bot_alias")

    def create_telegram_pairing_challenge(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        bot_alias: str,
        now_epoch: int,
        ttl_seconds: int = 600,
    ) -> str:
        """Create one high-entropy, short-lived pairing code.

        The plaintext code is returned once to the trusted admin workflow and is
        never stored.  This method is an internal persistence primitive; the
        caller must authorize the admin action before invoking it.
        """

        self._validate_bot_alias(bot_alias)
        self._require_active_actor(tenant_id, actor_id)
        if now_epoch < 0:
            raise ValueError("now_epoch must be non-negative")
        if ttl_seconds < 60 or ttl_seconds > 3600:
            raise ValueError("ttl_seconds must be between 60 and 3600")
        code = secrets.token_urlsafe(24)
        challenge_hash = hashlib.sha256(f"{bot_alias}:{code}".encode("utf-8")).hexdigest()
        self._db.execute(
            """
            INSERT INTO telegram_pairing_challenges(
                challenge_hash,bot_alias,tenant_id,actor_id,expires_at,used_at,created_at
            ) VALUES(?,?,?,?,?,NULL,?)
            """,
            (challenge_hash, bot_alias, tenant_id, actor_id, now_epoch + ttl_seconds, now_epoch),
        )
        self._db.commit()
        return code

    def consume_telegram_pairing_challenge(
        self,
        *,
        bot_alias: str,
        code: str,
        telegram_user_id: int,
        chat_id: int,
        now_epoch: int,
    ) -> TelegramBinding:
        self._validate_bot_alias(bot_alias)
        if not code or len(code) > 256:
            raise PolicyDenied("pairing unavailable")
        if telegram_user_id <= 0 or chat_id <= 0 or now_epoch < 0:
            raise PolicyDenied("pairing unavailable")
        challenge_hash = hashlib.sha256(f"{bot_alias}:{code}".encode("utf-8")).hexdigest()
        try:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute(
                """
                SELECT tenant_id,actor_id,expires_at,used_at
                FROM telegram_pairing_challenges
                WHERE challenge_hash=? AND bot_alias=?
                """,
                (challenge_hash, bot_alias),
            ).fetchone()
            if row is None or row["used_at"] is not None or int(row["expires_at"]) < now_epoch:
                raise PolicyDenied("pairing unavailable")
            tenant_id = str(row["tenant_id"])
            actor_id = str(row["actor_id"])
            self._require_active_actor(tenant_id, actor_id)
            existing = self._db.execute(
                "SELECT status FROM telegram_bindings WHERE bot_alias=? AND chat_id=?",
                (bot_alias, chat_id),
            ).fetchone()
            if existing is not None and existing["status"] == "ACTIVE":
                raise PolicyDenied("telegram chat already paired")
            changed = self._db.execute(
                """
                UPDATE telegram_pairing_challenges
                SET used_at=?
                WHERE challenge_hash=? AND used_at IS NULL
                """,
                (now_epoch, challenge_hash),
            ).rowcount
            if changed != 1:
                raise PolicyDenied("pairing unavailable")
            if existing is None:
                self._db.execute(
                    """
                    INSERT INTO telegram_bindings(
                        bot_alias,chat_id,telegram_user_id,tenant_id,actor_id,status,paired_at
                    ) VALUES(?,?,?,?,?,'ACTIVE',?)
                    """,
                    (bot_alias, chat_id, telegram_user_id, tenant_id, actor_id, now_epoch),
                )
            else:
                self._db.execute(
                    """
                    UPDATE telegram_bindings
                    SET telegram_user_id=?,tenant_id=?,actor_id=?,status='ACTIVE',paired_at=?
                    WHERE bot_alias=? AND chat_id=? AND status='REVOKED'
                    """,
                    (telegram_user_id, tenant_id, actor_id, now_epoch, bot_alias, chat_id),
                )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return TelegramBinding(
            bot_alias=bot_alias,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            status="ACTIVE",
        )

    def resolve_telegram_binding(
        self,
        *,
        bot_alias: str,
        telegram_user_id: int,
        chat_id: int,
    ) -> TelegramBinding:
        self._validate_bot_alias(bot_alias)
        if telegram_user_id <= 0 or chat_id <= 0:
            raise PolicyDenied("telegram identity unavailable")
        row = self._db.execute(
            """
            SELECT bot_alias,chat_id,telegram_user_id,tenant_id,actor_id,status
            FROM telegram_bindings
            WHERE bot_alias=? AND chat_id=? AND telegram_user_id=?
            """,
            (bot_alias, chat_id, telegram_user_id),
        ).fetchone()
        if row is None or row["status"] != "ACTIVE":
            raise PolicyDenied("telegram identity unavailable")
        self._require_active_actor(str(row["tenant_id"]), str(row["actor_id"]))
        return TelegramBinding(
            bot_alias=str(row["bot_alias"]),
            chat_id=int(row["chat_id"]),
            telegram_user_id=int(row["telegram_user_id"]),
            tenant_id=str(row["tenant_id"]),
            actor_id=str(row["actor_id"]),
            status=str(row["status"]),
        )

    def revoke_telegram_binding(
        self,
        *,
        tenant_id: str,
        bot_alias: str,
        chat_id: int,
    ) -> None:
        self._validate_bot_alias(bot_alias)
        self._require_active_tenant(tenant_id)
        changed = self._db.execute(
            """
            UPDATE telegram_bindings SET status='REVOKED'
            WHERE tenant_id=? AND bot_alias=? AND chat_id=? AND status='ACTIVE'
            """,
            (tenant_id, bot_alias, chat_id),
        ).rowcount
        self._db.commit()
        if changed != 1:
            raise PolicyDenied("telegram identity unavailable")

    def claim_telegram_update(
        self,
        *,
        bot_alias: str,
        update_id: int,
        received_at: int,
    ) -> None:
        """Atomically claim one Telegram update ID; duplicates fail closed."""

        self._validate_bot_alias(bot_alias)
        if update_id < 0 or received_at < 0:
            raise PolicyDenied("telegram update unavailable")
        try:
            self._db.execute(
                "INSERT INTO telegram_updates(bot_alias,update_id,received_at) VALUES(?,?,?)",
                (bot_alias, update_id, received_at),
            )
            self._db.commit()
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            raise PolicyDenied("telegram update replayed") from exc

    def prune_telegram_updates(self, *, before_epoch: int) -> int:
        if before_epoch < 0:
            raise ValueError("before_epoch must be non-negative")
        changed = self._db.execute(
            "DELETE FROM telegram_updates WHERE received_at < ?",
            (before_epoch,),
        ).rowcount
        self._db.commit()
        return changed

    def append_audit(
        self,
        *,
        context: ExecutionContext,
        event: str,
        result: str,
        capability: str | None = None,
        resource_scope: str | None = None,
        sensitive_payload: Mapping[str, Any] | None = None,
    ) -> int:
        self._require_active_tenant(context.tenant_id)
        payload_json = json.dumps(sensitive_payload or {}, sort_keys=True, separators=(",", ":"))
        payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        prev = self._db.execute(
            "SELECT event_hash FROM audit_events WHERE tenant_id=? ORDER BY sequence DESC LIMIT 1",
            (context.tenant_id,),
        ).fetchone()
        prev_hash = str(prev["event_hash"]) if prev else "GENESIS"
        created_at = datetime.now(UTC).isoformat()
        material = json.dumps(
            {
                "tenant_id": context.tenant_id,
                "actor_id": context.actor_id,
                "event": event,
                "capability": capability,
                "resource_scope": resource_scope,
                "correlation_id": context.correlation_id,
                "result": result,
                "payload_digest": payload_digest,
                "created_at": created_at,
                "prev_hash": prev_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        event_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        cur = self._db.execute(
            """
            INSERT INTO audit_events(
                tenant_id,actor_id,event,capability,resource_scope,correlation_id,
                result,payload_digest,created_at,prev_hash,event_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                context.tenant_id,
                context.actor_id,
                event,
                capability,
                resource_scope,
                context.correlation_id,
                result,
                payload_digest,
                created_at,
                prev_hash,
                event_hash,
            ),
        )
        self._db.commit()
        lastrowid = cur.lastrowid
        if lastrowid is None:
            raise RuntimeError("audit insert did not return a row id")
        return lastrowid

    def audit_records(self, tenant_id: str) -> list[AuditRecord]:
        rows = self._db.execute(
            "SELECT sequence,tenant_id,actor_id,event,capability,resource_scope,correlation_id,result,created_at,event_hash "
            "FROM audit_events WHERE tenant_id=? ORDER BY sequence",
            (tenant_id,),
        ).fetchall()
        return [AuditRecord(**dict(r)) for r in rows]

    def verify_audit_chain(self, tenant_id: str) -> bool:
        rows = self._db.execute(
            "SELECT * FROM audit_events WHERE tenant_id=? ORDER BY sequence",
            (tenant_id,),
        ).fetchall()
        prev_hash = "GENESIS"
        for row in rows:
            if row["prev_hash"] != prev_hash:
                return False
            material = json.dumps(
                {
                    "tenant_id": row["tenant_id"],
                    "actor_id": row["actor_id"],
                    "event": row["event"],
                    "capability": row["capability"],
                    "resource_scope": row["resource_scope"],
                    "correlation_id": row["correlation_id"],
                    "result": row["result"],
                    "payload_digest": row["payload_digest"],
                    "created_at": row["created_at"],
                    "prev_hash": row["prev_hash"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            expected = hashlib.sha256(material.encode("utf-8")).hexdigest()
            if row["event_hash"] != expected:
                return False
            prev_hash = str(row["event_hash"])
        return True

    def _same_tenant(self, context: ExecutionContext, owner_tenant_id: str) -> None:
        if context.tenant_id != owner_tenant_id:
            # Do not reveal whether the foreign object exists.
            raise PolicyDenied("resource unavailable")

    def _require_active_tenant(self, tenant_id: str) -> None:
        row = self._db.execute(
            "SELECT status FROM tenants WHERE tenant_id=?",
            (tenant_id,),
        ).fetchone()
        if row is None or row["status"] != "ACTIVE":
            raise PolicyDenied("tenant unavailable")

    def _require_active_actor(self, tenant_id: str, actor_id: str) -> None:
        self._require_active_tenant(tenant_id)
        row = self._db.execute(
            "SELECT status FROM actors WHERE tenant_id=? AND actor_id=?",
            (tenant_id, actor_id),
        ).fetchone()
        if row is None or row["status"] != "ACTIVE":
            raise PolicyDenied("actor unavailable")
