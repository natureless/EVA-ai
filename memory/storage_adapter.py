"""Storage adapter abstraction — contract all storage backends must fulfill.

Currently only SQLiteStore is wired; PostgresStore was removed as dead code
(no caller outside tests).  When a second backend is needed, implement
BaseStorageAdapter and update bootstrap to select it.
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
