"""Atomic SQLite backup/restore primitives for OS AI Core F8."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class BackupError(RuntimeError):
    """Backup or restore validation failed."""


@dataclass(frozen=True)
class BackupArtifact:
    path: str
    sha256: str
    size_bytes: int
    created_at: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity_check(path: Path) -> None:
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = db.execute("PRAGMA integrity_check").fetchone()
        finally:
            db.close()
    except sqlite3.DatabaseError as exc:
        raise BackupError("SQLite integrity check failed") from exc
    if row is None or row[0] != "ok":
        raise BackupError("SQLite integrity check failed")


def backup_sqlite(source_path: str | Path, destination_path: str | Path) -> BackupArtifact:
    source = Path(source_path)
    destination = Path(destination_path)
    if not source.is_file():
        raise BackupError("source database does not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        source_db = sqlite3.connect(str(source))
        target_db = sqlite3.connect(str(tmp))
        try:
            source_db.backup(target_db)
            target_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            target_db.commit()
        finally:
            target_db.close()
            source_db.close()
        _integrity_check(tmp)
        os.chmod(tmp, 0o600)
        os.replace(tmp, destination)
    finally:
        if tmp.exists():
            tmp.unlink()
    return BackupArtifact(
        path=str(destination),
        sha256=_sha256(destination),
        size_bytes=destination.stat().st_size,
        created_at=datetime.now(UTC).isoformat(),
    )


def restore_sqlite(
    backup_path: str | Path,
    destination_path: str | Path,
    *,
    expected_sha256: str,
    overwrite: bool = False,
) -> None:
    source = Path(backup_path)
    destination = Path(destination_path)
    if not source.is_file():
        raise BackupError("backup does not exist")
    if not expected_sha256 or _sha256(source) != expected_sha256:
        raise BackupError("backup checksum mismatch")
    if destination.exists() and not overwrite:
        raise BackupError("restore destination already exists")
    _integrity_check(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.restore")
    try:
        source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        target_db = sqlite3.connect(str(tmp))
        try:
            source_db.backup(target_db)
            target_db.commit()
        finally:
            target_db.close()
            source_db.close()
        _integrity_check(tmp)
        os.chmod(tmp, 0o600)
        os.replace(tmp, destination)
    finally:
        if tmp.exists():
            tmp.unlink()
