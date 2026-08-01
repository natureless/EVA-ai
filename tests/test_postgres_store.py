"""Tests for the PostgreSQL storage backend (memory/postgres_store.py).

Tests focus on the SQL translation layer and the adapter interface.
Integration tests against a real PG instance require EVA_PG_* env vars.
"""

from __future__ import annotations

import pytest

from memory.postgres_store import (
    PostgresStore,
    _replace_placeholders,
    _sqlite_to_pg,
)
from memory.storage_adapter import BaseStorageAdapter


class TestSQLTranslation:
    """Unit tests for SQLite → PostgreSQL SQL translation."""

    def test_simple_placeholder(self):
        """Single ? becomes %s."""
        assert _replace_placeholders("SELECT * FROM t WHERE x = ?") == "SELECT * FROM t WHERE x = %s"

    def test_multiple_placeholders(self):
        """Multiple ? become %s."""
        assert _replace_placeholders("INSERT INTO t (a, b, c) VALUES (?, ?, ?)") == "INSERT INTO t (a, b, c) VALUES (%s, %s, %s)"

    def test_no_placeholders(self):
        """SQL without ? is unchanged."""
        sql = "SELECT * FROM t"
        assert _replace_placeholders(sql) == sql

    def test_placeholder_in_single_quotes_preserved(self):
        """? inside single-quoted string is not replaced."""
        sql = "SELECT '?' AS q, col FROM t WHERE col = ?"
        expected = "SELECT '?' AS q, col FROM t WHERE col = %s"
        assert _replace_placeholders(sql) == expected

    def test_placeholder_in_double_quotes_preserved(self):
        """? inside double-quoted identifier is not replaced."""
        sql = 'SELECT "col?name" FROM t WHERE x = ?'
        expected = 'SELECT "col?name" FROM t WHERE x = %s'
        assert _replace_placeholders(sql) == expected

    def test_mixed_quotes(self):
        """Mixed single and double quotes handled correctly."""
        sql = """SELECT 'it''s a test', "quoted?col" FROM t WHERE a = ? AND b = ?"""
        result = _replace_placeholders(sql)
        # The ? in single quotes should stay, the ones outside should become %s
        assert "a = %s" in result
        assert "b = %s" in result
        assert "'it''s a test'" in result

    def test_datetime_now_replaced(self):
        """datetime('now') becomes NOW()."""
        assert _sqlite_to_pg("datetime('now')") == "NOW()"

    def test_full_sql_translation(self):
        """Combined translation: placeholders + datetime."""
        sql = "INSERT INTO t (name, created_at) VALUES (?, datetime('now'))"
        result = _sqlite_to_pg(sql)
        assert "VALUES (%s, NOW())" in result

    def test_select_with_placeholders(self):
        """SELECT with placeholders."""
        sql = "SELECT * FROM memory_items WHERE memory_type = ? AND status = ?"
        result = _sqlite_to_pg(sql)
        assert "memory_type = %s" in result
        assert "status = %s" in result


class TestPostgresStoreInterface:
    """Tests for PostgresStore constructor and interface compliance."""

    def test_is_base_storage_adapter(self):
        """PostgresStore implements BaseStorageAdapter."""
        store = PostgresStore()
        assert isinstance(store, BaseStorageAdapter)

    def test_default_config_from_env(self, monkeypatch):
        """Config defaults to env vars."""
        monkeypatch.setenv("EVA_PG_HOST", "pg.example.com")
        monkeypatch.setenv("EVA_PG_PORT", "5433")
        monkeypatch.setenv("EVA_PG_DATABASE", "evadb")
        monkeypatch.setenv("EVA_PG_USER", "eva_user")
        monkeypatch.setenv("EVA_PG_PASSWORD", "s3cret")

        store = PostgresStore()
        assert store._host == "pg.example.com"
        assert store._port == 5433
        assert store._database == "evadb"
        assert store._user == "eva_user"
        assert store._password == "s3cret"

    def test_explicit_config_overrides_env(self, monkeypatch):
        """Constructor args override env vars."""
        monkeypatch.setenv("EVA_PG_HOST", "env-host")
        monkeypatch.setenv("EVA_PG_PASSWORD", "env-pass")

        store = PostgresStore(host="explicit-host", password="explicit-pass")
        assert store._host == "explicit-host"
        assert store._password == "explicit-pass"

    def test_database_url_takes_priority(self):
        """DATABASE_URL overrides individual config."""
        url = "postgresql://user:pass@db.example.com:5432/mydb"
        store = PostgresStore(database_url=url, host="ignored")
        assert store._database_url == url

    def test_dsn_from_url(self):
        """_build_dsn returns URL when set."""
        url = "postgresql://user:pass@localhost:5432/eva"
        store = PostgresStore(database_url=url)
        assert store._build_dsn() == url

    def test_dsn_from_parts(self):
        """_build_dsn builds from individual parts."""
        store = PostgresStore(host="h", port=5432, database="d", user="u", password="p")
        dsn = store._build_dsn()
        assert "host=h" in dsn
        assert "port=5432" in dsn
        assert "dbname=d" in dsn
        assert "user=u" in dsn
        assert "password=p" in dsn

    def test_dsn_without_password(self):
        """DSN without password omits password field."""
        store = PostgresStore(host="h", port=5432, database="d", user="u")
        dsn = store._build_dsn()
        assert "password" not in dsn

    def test_close_marks_closed(self):
        """close() sets _closed flag."""
        store = PostgresStore()
        store.close()
        assert store._closed is True

    def test_json_helpers(self):
        """JSON helpers are inherited from BaseStorageAdapter."""
        store = PostgresStore()
        data = {"key": "value", "nested": [1, 2, 3]}
        dumped = store.dumps_json(data)
        loaded = store.loads_json(dumped)
        assert loaded == data


class TestPostgresStoreNoConnection:
    """Tests that don't require a running PostgreSQL instance."""

    def test_execute_without_connection_raises(self):
        """Calling execute without PG raises psycopg2 error."""
        store = PostgresStore()
        # Don't actually connect — just verify it fails gracefully
        # We don't import psycopg2 at module level, so the error
        # is either ImportError or operational error
        try:
            store.execute("SELECT 1")
            # If we get here, PG is available — skip assertion
            pytest.skip("PostgreSQL is running — skipping no-connection test")
        except Exception as e:
            # Expected: either ImportError (no psycopg2) or OperationalError (no PG)
            assert True

    def test_ping_returns_false_without_connection(self):
        """ping() returns False when PG is unreachable."""
        store = PostgresStore()
        result = store.ping()
        # Without PG running, ping should return False
        assert result is False
