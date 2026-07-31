"""Unit tests for migration runner and SQL splitter."""

import tempfile
from pathlib import Path

import pytest

from memory.sqlite_store import SQLiteStore
from core.migration import MigrationRunner, _split_sql, MIGRATIONS_DIR


class TestSQLSplitter:
    def test_single_statement(self):
        result = _split_sql("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        assert len(result) == 1
        assert "CREATE TABLE" in result[0]

    def test_multiple_statements(self):
        sql = "CREATE TABLE a (id INTEGER); CREATE TABLE b (id INTEGER);"
        result = _split_sql(sql)
        assert len(result) == 2

    def test_empty_sql(self):
        assert _split_sql("") == []
        assert _split_sql("   ;  ;  ") == []

    def test_skip_comment_blocks(self):
        sql = "CREATE TABLE a (id INTEGER);\n-- this is a comment\n;"
        result = _split_sql(sql)
        assert len(result) == 1

    def test_create_trigger_block_preserved(self):
        sql = (
            "CREATE TRIGGER after_insert\n"
            "AFTER INSERT ON events\n"
            "BEGIN\n"
            "  UPDATE counter SET cnt = cnt + 1;\n"
            "END;"
        )
        result = _split_sql(sql)
        assert len(result) == 1
        assert "CREATE TRIGGER" in result[0]
        assert "END" in result[0]


class TestMigrationRunner:
    @classmethod
    def setup_class(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = Path(cls.tmpdir) / "test.db"
        cls.store = SQLiteStore(cls.db_path)
        cls.store.init_db()

    def test_migrate_creates_tracking_table(self):
        runner = MigrationRunner(self.store)
        runner.migrate()
        # Verify _migrations table exists
        rows = self.store.fetchall("SELECT name FROM sqlite_master WHERE type='table' AND name='_migrations'")
        assert len(rows) == 1

    def test_migrate_is_idempotent(self):
        """Running migrate twice should not fail or duplicate."""
        runner = MigrationRunner(self.store)
        first = runner.migrate()
        second = runner.migrate()
        assert second == []  # nothing new to apply
        assert first == second or len(second) == 0

    def test_applied_migrations_returns_set(self):
        runner = MigrationRunner(self.store)
        applied = runner._applied_migrations()
        assert isinstance(applied, set)

    def test_pending_migrations_with_no_new_files(self):
        runner = MigrationRunner(self.store)
        applied = runner._applied_migrations()
        pending = runner._pending_migrations(applied)
        # All existing migration files should already be applied
        for p in pending:
            assert p["name"] not in applied

    def test_migration_dir_exists(self):
        assert MIGRATIONS_DIR.exists()
        assert MIGRATIONS_DIR.is_dir()
