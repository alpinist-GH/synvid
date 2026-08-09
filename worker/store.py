"""SQLite index with immutable sidecars and transactional schema migrations."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
from typing import Iterable
import uuid


SCHEMA_VERSION = 2


class StoreError(RuntimeError):
    pass


class UnsupportedStoreVersion(StoreError):
    pass


class Store:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def open(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(self.database_path)
            check = connection.execute("PRAGMA quick_check").fetchone()
            if check != ("ok",):
                raise sqlite3.DatabaseError("SQLite quick_check failed")
        except sqlite3.DatabaseError as error:
            self._preserve_corrupt_index()
            raise StoreError("index is corrupt; it was preserved for diagnostics") from error
        connection.execute("PRAGMA foreign_keys = ON")
        current = connection.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            connection.close()
            raise UnsupportedStoreVersion("database was created by a newer SynVid")
        if current < SCHEMA_VERSION:
            self._backup()
            self._migrate(connection, current)
        return connection

    def _migrate(self, connection: sqlite3.Connection, current: int) -> None:
        """Apply every ordered migration in one transaction so a retry is safe."""
        connection.execute("BEGIN IMMEDIATE")
        try:
            while current < SCHEMA_VERSION:
                next_version = current + 1
                self._apply_migration(connection, next_version)
                connection.execute(f"PRAGMA user_version = {next_version}")
                current = next_version
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration(connection: sqlite3.Connection, version: int) -> None:
        if version == 1:
            connection.execute("CREATE TABLE IF NOT EXISTS outputs (output_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)")
        elif version == 2:
            connection.execute("CREATE TABLE IF NOT EXISTS migration_state (schema_version INTEGER NOT NULL)")
            connection.execute("INSERT OR REPLACE INTO migration_state(rowid, schema_version) VALUES(1, 2)")
        else:  # Defensive: a bump without an implementation must never silently advance.
            raise StoreError(f"missing migration for schema version {version}")

    def _backup(self) -> None:
        if self.database_path.exists():
            shutil.copy2(self.database_path, self.database_path.with_suffix(self.database_path.suffix + ".backup"))

    def _preserve_corrupt_index(self) -> Path | None:
        if not self.database_path.exists():
            return None
        preserved = self.database_path.with_name(f"{self.database_path.name}.corrupt-{uuid.uuid4()}")
        shutil.move(self.database_path, preserved)
        return preserved

    def rebuild(self, sidecar_paths: Iterable[Path]) -> int:
        connection = self.open()
        count = 0
        try:
            with connection:
                connection.execute("DELETE FROM outputs")
                for path in sidecar_paths:
                    try:
                        raw = json.loads(path.read_text())
                    except (OSError, json.JSONDecodeError):
                        continue
                    output_id = raw.get("output_id")
                    if not isinstance(output_id, str) or not output_id or not isinstance(raw.get("schema_version", 1), int):
                        continue
                    connection.execute("INSERT INTO outputs(output_id, metadata_json) VALUES(?, ?)", (output_id, json.dumps(raw, sort_keys=True)))
                    count += 1
        finally:
            connection.close()
        return count

    def remove_output(self, output_id: str) -> None:
        """Remove only one already-deleted output from the searchable index."""
        connection = self.open()
        try:
            with connection:
                connection.execute("DELETE FROM outputs WHERE output_id = ?", (output_id,))
        finally:
            connection.close()
