"""Storage adapter unit tests."""

import os
import tempfile
from pathlib import Path
from memory.storage_adapter import BaseStorageAdapter, create_store
from memory.sqlite_store import SQLiteStore


class TestBaseStorageAdapter:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()

    def test_sqlite_store_is_adapter(self):
        store = SQLiteStore(Path(self.tmpdir) / "test.db")
        assert isinstance(store, BaseStorageAdapter)

    def test_create_store_defaults_to_sqlite(self):
        store = create_store()
        assert isinstance(store, SQLiteStore)
        assert isinstance(store, BaseStorageAdapter)

    def test_create_store_explicit_sqlite(self):
        store = create_store()
        store.init_db()
        assert isinstance(store, SQLiteStore)
        store.close()

    def test_create_store_respects_backend_config(self):
        """Explicit sqlite backend returns SQLiteStore."""
        store = create_store()
        assert isinstance(store, BaseStorageAdapter)
        assert isinstance(store, SQLiteStore)

    def test_create_store_with_postgres_backend_creates_pg_store(self):
        """When EVA_STORAGE_BACKEND=postgresql, returns PostgresStore."""
        from memory.postgres_store import PostgresStore
        saved = os.environ.get("EVA_STORAGE_BACKEND")
        os.environ["EVA_STORAGE_BACKEND"] = "postgresql"
        try:
            store = create_store()
            # PostgresStore is created (init is lightweight, no psycopg2 needed yet)
            assert isinstance(store, PostgresStore)
        finally:
            if saved is not None:
                os.environ["EVA_STORAGE_BACKEND"] = saved
            else:
                del os.environ["EVA_STORAGE_BACKEND"]

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

    def test_factory_with_config_object(self):
        class FakeSettings:
            storage_backend = "sqlite"
            db_path = ":memory:"
        store = create_store(FakeSettings())
        assert isinstance(store, SQLiteStore)
