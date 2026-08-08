import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from worker.store import Store, StoreError, UnsupportedStoreVersion


class StoreTests(unittest.TestCase):
    def test_migration_backups_and_rebuilds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "index.sqlite3"
            sqlite3.connect(db).close()
            sidecar = root / "metadata.json"
            sidecar.write_text(json.dumps({"output_id": "output-1", "lineage": []}))
            store = Store(db)
            self.assertEqual(store.rebuild([sidecar]), 1)
            self.assertTrue((root / "index.sqlite3.backup").exists())

    def test_refuses_newer_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "index.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA user_version = 99")
            connection.close()
            with self.assertRaises(UnsupportedStoreVersion):
                Store(path).open()

    def test_failed_migration_rolls_back_and_can_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "index.sqlite3"
            store = Store(path)
            original = store._apply_migration

            def fail_second_version(connection, version):
                if version == 2:
                    raise sqlite3.OperationalError("simulated migration failure")
                return original(connection, version)

            store._apply_migration = fail_second_version
            with self.assertRaises(sqlite3.OperationalError):
                store.open()
            connection = sqlite3.connect(path)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            connection.close()
            store._apply_migration = original
            connection = store.open()
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            connection.close()

    def test_preserves_corrupt_index(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "index.sqlite3"
            path.write_text("not a sqlite database")
            with self.assertRaises(StoreError):
                Store(path).open()
            self.assertEqual(list(Path(temp).glob("index.sqlite3.corrupt-*")), [next(Path(temp).glob("index.sqlite3.corrupt-*"))])
