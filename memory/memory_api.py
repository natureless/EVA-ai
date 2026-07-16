import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from event.event_schema import Event, TraceRecord
from memory.sqlite_store import SQLiteStore


logger = logging.getLogger("eva.memory_api")


@dataclass
class EpisodicMemoryItem:
    """Represents a single episodic memory entry.
    
    Attributes:
        id: Unique memory identifier
        timestamp: ISO timestamp of when memory was recorded
        event_type: Type of event that created this memory
        summary: Text summary of the memory
        payload: Associated data and metadata
        importance: Importance score (0-1)
    """
    id: str
    timestamp: str
    event_type: str
    summary: str
    payload: dict
    importance: float


class MemoryAPI:
    """API for reading and writing memories and traces.
    
    Provides methods to persist and retrieve:
    - Events: Raw system events
    - Episodic memories: Processed event summaries
    - Traces: Execution traces from cognition loop
    """

    def __init__(self, store: SQLiteStore) -> None:
        """Initialize memory API with a SQLite store.
        
        Args:
            store: SQLiteStore instance for database access
        """
        self.store = store

    def append_event(self, event: Event) -> None:
        """Persist an event to the database.
        
        Args:
            event: Event object to store
        """
        try:
            self.store.execute(
                """
                INSERT INTO events (id, type, source, timestamp, payload, correlation_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.type,
                    event.source,
                    event.timestamp.isoformat(),
                    self.store.dumps_json(event.payload),
                    event.correlation_id,
                    event.status,
                ),
            )
            logger.debug("event stored: %s", event.id)
        except Exception as e:
            logger.error("failed to store event %s: %s", event.id, e, exc_info=True)
            raise

    def write_trace(self, trace: TraceRecord) -> None:
        """Persist an execution trace to the database.
        
        Args:
            trace: TraceRecord object to store
        """
        try:
            self.store.execute(
                """
                INSERT INTO traces (id, loop_id, timestamp, event_type, decision, agent, result_summary, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.id,
                    trace.loop_id,
                    trace.timestamp.isoformat(),
                    trace.event_type,
                    trace.decision,
                    trace.agent,
                    trace.result_summary,
                    trace.duration_ms,
                ),
            )
            logger.debug("trace stored: %s", trace.loop_id)
        except Exception as e:
            logger.error("failed to store trace %s: %s", trace.loop_id, e, exc_info=True)
            raise

    def write_episodic_memory(
        self,
        event_type: str,
        summary: str,
        payload: dict[str, Any],
        importance: float = 0.5,
    ) -> str:
        """Store an episodic memory entry.
        
        Args:
            event_type: Type of event this memory came from
            summary: Text summary of the memory
            payload: Associated data
            importance: Importance score (default: 0.5)
            
        Returns:
            Generated memory ID
        """
        memory_id = str(uuid4())
        ts = datetime.now(timezone.utc).isoformat()
        try:
            self.store.execute(
                """
                INSERT INTO episodic_memory (id, timestamp, event_type, summary, payload, importance)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    ts,
                    event_type,
                    summary,
                    self.store.dumps_json(payload),
                    importance,
                ),
            )
            logger.debug("episodic memory stored: %s", memory_id)
            return memory_id
        except Exception as e:
            logger.error("failed to store episodic memory: %s", e, exc_info=True)
            raise

    def get_recent_memories(self, limit: int = 20) -> list[dict]:
        """Retrieve recent episodic memories.
        
        Args:
            limit: Maximum number of memories to retrieve
            
        Returns:
            List of recent memory entries in reverse chronological order
        """
        rows = self.store.fetchall(
            """
            SELECT id, timestamp, event_type, summary, payload, importance
            FROM episodic_memory
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        for row in rows:
            row["payload"] = self.store.loads_json(row["payload"])
        return rows

    def get_recent_traces(self, limit: int = 20) -> list[dict]:
        """Retrieve recent execution traces.
        
        Args:
            limit: Maximum number of traces to retrieve
            
        Returns:
            List of recent traces in reverse chronological order
        """
        rows = self.store.fetchall(
            """
            SELECT id, loop_id, timestamp, event_type, decision, agent, result_summary, duration_ms
            FROM traces
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        logger.debug("retrieved %d traces", len(rows))
        return rows

    def get_recent_events(self, limit: int = 20) -> list[dict]:
        """Retrieve recent events.
        
        Args:
            limit: Maximum number of events to retrieve
            
        Returns:
            List of recent events in reverse chronological order
        """
        rows = self.store.fetchall(
            """
            SELECT id, type, source, timestamp, payload, correlation_id, status
            FROM events
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        for row in rows:
            row["payload"] = self.store.loads_json(row["payload"])
        logger.debug("retrieved %d events", len(rows))
        return rows

