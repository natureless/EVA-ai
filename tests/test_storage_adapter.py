"""Storage adapter unit tests."""

import tempfile
from pathlib import Path
from memory.storage_adapter import BaseStorageAdapter
from memory.sqlite_store import SQLiteStore


class TestBaseStorageAdapter:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()

    def test_sqlite_store_is_adapter(self):
        store = SQLiteStore(Path(self.tmpdir) / "test.db")
        assert isinstance(store, BaseStorageAdapter)

    def test_sqlite_store_has_all_methods(self):
        store = SQLiteStore(Path(self.tmpdir) / "methods.db")
        store.init_db()
        # execute
        store.execute("CREATE TABLE IF NOT EXISTS test (id TEXT)")
        store.execute("INSERT INTO test VALUES (?)", ("x",))
        # fetchone
        row = store.fetchone("SELECT * FROM test")
        assert row["id"] == "x"
        # fetchall
        store.execute("INSERT INTO test VALUES (?)", ("y",))
        rows = store.fetchall("SELECT * FROM test ORDER BY id")
        assert len(rows) == 2
        # execute_many
        store.execute_many("INSERT INTO test VALUES (?)", [("a",), ("b",)])
        assert len(store.fetchall("SELECT * FROM test")) == 4
        store.close()


class TestStorageAbstraction:
    def test_json_helpers(self):
        from datetime import datetime
        data = {"name": "test", "ts": datetime(2026, 7, 16)}
        encoded = BaseStorageAdapter.dumps_json(data)
        assert "test" in encoded
        decoded = BaseStorageAdapter.loads_json(encoded)
        assert decoded["name"] == "test"
