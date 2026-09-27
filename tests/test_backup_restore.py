import os
import tempfile
import unittest
from pathlib import Path

from osai.backup import BackupError, backup_sqlite, restore_sqlite
from osai.contracts import ExecutionContext
from osai.storage import TenantSecurityStore


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.sqlite3"
        self.backup = self.root / "backups" / "snapshot.sqlite3"
        self.restored = self.root / "restored.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def _seed(self):
        store = TenantSecurityStore(self.source)
        store.add_tenant("alpha")
        store.add_actor("alpha", "owner", role="TENANT_ADMIN")
        context = ExecutionContext.issue(tenant_id="alpha", actor_id="owner")
        store.append_audit(context=context, event="seed.created", result="OK")
        store.close()

    def test_backup_and_restore_preserve_database_and_audit_chain(self):
        self._seed()
        artifact = backup_sqlite(self.source, self.backup)
        self.assertTrue(self.backup.is_file())
        self.assertEqual(len(artifact.sha256), 64)
        if os.name != "nt":
            self.assertEqual(self.backup.stat().st_mode & 0o777, 0o600)

        # Mutate the original after the snapshot; the restored copy must still
        # reflect the exact snapshot rather than the later source state.
        with TenantSecurityStore(self.source) as store:
            store.add_actor("alpha", "later-user")

        restore_sqlite(
            self.backup,
            self.restored,
            expected_sha256=artifact.sha256,
        )
        with TenantSecurityStore(self.restored) as restored:
            actors = restored._db.execute(
                "SELECT actor_id FROM actors WHERE tenant_id='alpha' ORDER BY actor_id"
            ).fetchall()
            self.assertEqual([row["actor_id"] for row in actors], ["owner"])
            self.assertTrue(restored.verify_audit_chain("alpha"))

    def test_restore_rejects_checksum_mismatch_and_existing_destination(self):
        self._seed()
        artifact = backup_sqlite(self.source, self.backup)
        with self.assertRaises(BackupError):
            restore_sqlite(
                self.backup,
                self.restored,
                expected_sha256="0" * 64,
            )
        restore_sqlite(
            self.backup,
            self.restored,
            expected_sha256=artifact.sha256,
        )
        with self.assertRaises(BackupError):
            restore_sqlite(
                self.backup,
                self.restored,
                expected_sha256=artifact.sha256,
            )


if __name__ == "__main__":
    unittest.main()
