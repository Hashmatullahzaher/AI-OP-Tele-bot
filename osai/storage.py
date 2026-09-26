"""Tenant-isolated SQLite persistence for identity, policy, refs, and audit.

This is an F1 foundation, not a complete production identity provider.  It stores
only secret *references*, never secret values, and uses compound tenant keys.
"""

from __future__ import annotations

import hashlib
import json
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
                role TEXT NOT NULL CHECK(role IN ('TENANT_USER','TENANT_ADMIN','PLATFORM_OPERATOR')),
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
            """
        )
        # One-way compatibility migration for pre-role foundation databases.
        actor_columns = {str(row["name"]) for row in self._db.execute("PRAGMA table_info(actors)").fetchall()}
        if "role" not in actor_columns:
            self._db.execute("ALTER TABLE actors ADD COLUMN role TEXT NOT NULL DEFAULT 'TENANT_USER'")
        self._db.commit()

    def add_tenant(self, tenant_id: str) -> None:
        self._db.execute(
            "INSERT INTO tenants(tenant_id,status) VALUES(?, 'ACTIVE')",
            (tenant_id,),
        )
        self._db.commit()

    def add_actor(self, tenant_id: str, actor_id: str, *, role: str = "TENANT_USER") -> None:
        self._require_active_tenant(tenant_id)
        if role not in {"TENANT_USER", "TENANT_ADMIN", "PLATFORM_OPERATOR"}:
            raise ValueError("unsupported actor role")
        self._db.execute(
            "INSERT INTO actors(tenant_id,actor_id,role,status) VALUES(?, ?, ?, 'ACTIVE')",
            (tenant_id, actor_id, role),
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
        role = self._require_active_actor(tenant_id, actor_id)
        if role == "PLATFORM_OPERATOR" and not capability.startswith("platform."):
            raise PolicyDenied("platform operators cannot receive tenant-data capabilities")
        self._db.execute(
            "INSERT INTO grants(tenant_id,actor_id,capability,resource_scope) VALUES(?,?,?,?)",
            (tenant_id, actor_id, capability, resource_scope),
        )
        self._db.commit()

    def authorize(self, context: ExecutionContext, manifest: CapabilityManifest) -> None:
        role = self._require_active_actor(context.tenant_id, context.actor_id)
        if role == "PLATFORM_OPERATOR" and not manifest.capability.startswith("platform."):
            self.append_audit(
                context=context,
                event="policy.deny",
                capability=manifest.capability,
                resource_scope=",".join(sorted(set(context.resource_scope) or {"*"})),
                result="DENIED",
            )
            raise PolicyDenied("tenant data capability denied")
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
        self._require_tenant_actor(context.tenant_id, context.actor_id)
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
        self._require_tenant_actor(context.tenant_id, context.actor_id)
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

    def _require_active_actor(self, tenant_id: str, actor_id: str) -> str:
        self._require_active_tenant(tenant_id)
        row = self._db.execute(
            "SELECT status,role FROM actors WHERE tenant_id=? AND actor_id=?",
            (tenant_id, actor_id),
        ).fetchone()
        if row is None or row["status"] != "ACTIVE":
            raise PolicyDenied("actor unavailable")
        return str(row["role"])

    def _require_tenant_actor(self, tenant_id: str, actor_id: str) -> str:
        role = self._require_active_actor(tenant_id, actor_id)
        if role == "PLATFORM_OPERATOR":
            raise PolicyDenied("tenant resource unavailable")
        return role
