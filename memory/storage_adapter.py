"""Storage adapter abstraction — enables SQLite ↔ PostgreSQL interchange.

Usage in bootstrap::

    from memory.storage_adapter import create_store
    store = create_store(settings)  # auto-detects backend from config

All store implementations conform to BaseStorageAdapter.
"""

from abc import ABC, abstractmethod
from typing import Any, Iterable


class BaseStorageAdapter(ABC):
    """Contract all storage backends must fulfill."""

    @abstractmethod
    def init_db(self) -> None:
        """Initialize all tables and indexes (CREATE IF NOT EXISTS)."""
        ...

    @abstractmethod
    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        """Execute a write statement (INSERT/UPDATE/DELETE)."""
        ...

    @abstractmethod
    def execute_many(self, sql: str, params_list: Iterable[Iterable[Any]]) -> None:
        """Execute multiple writes in a single transaction."""
        ...

    @abstractmethod
    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        """Execute a read query and return all rows as dicts."""
        ...

    @abstractmethod
    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        """Execute a read query and return the first row, or None."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Release connections and clean up."""
        ...

    # ── JSON helpers (shared logic, concrete) ───────────────

    @staticmethod
    def dumps_json(value: Any) -> str:
        import json
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def loads_json(value: str) -> Any:
        import json
        return json.loads(value)


# ── Factory ─────────────────────────────────────────────────

def create_store(settings: Any = None) -> BaseStorageAdapter:
    """Create the appropriate storage backend from configuration.

    Reads EVA_STORAGE_BACKEND from settings or environment.
    Default: sqlite (always available, no dependencies).
    """
    backend = "sqlite"
    if settings is not None:
        backend = getattr(settings, "storage_backend", "sqlite") or "sqlite"
    else:
        import os
        backend = os.environ.get("EVA_STORAGE_BACKEND", "sqlite")

    backend = backend.lower().strip()

    if backend == "postgresql" or backend == "postgres" or backend == "pg":
        try:
            from memory.postgres_store import PostgresStore
            db_url = ""
            if settings is not None:
                db_url = getattr(settings, "database_url", "") or ""
            if not db_url:
                db_url = _pg_url_from_env()
            return PostgresStore(db_url)
        except ImportError:
            import logging
            logging.getLogger("eva.storage").warning(
                "psycopg2 not installed — falling back to SQLite"
            )

    # default: SQLite
    from pathlib import Path
    db_path = Path("data/eva.db")
    if settings is not None:
        db_path = Path(getattr(settings, "db_path", db_path))
    from memory.sqlite_store import SQLiteStore
    return SQLiteStore(db_path)


def _pg_url_from_env() -> str:
    import os
    host = os.environ.get("EVA_PG_HOST", "localhost")
    port = os.environ.get("EVA_PG_PORT", "5432")
    db = os.environ.get("EVA_PG_DATABASE", "eva")
    user = os.environ.get("EVA_PG_USER", "eva")
    password = os.environ.get("EVA_PG_PASSWORD", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"
