"""Durable storage for EPI-01 episode records."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time

from memory.episodes import (
    EpisodeRecord,
    EpisodeSchemaError,
    read_episode,
    replay_projection,
)


class DuplicateEpisodeError(ValueError):
    """An event already has an immutable episode record."""


class EpisodeStore:
    """Append-only SQLite store keyed by both episode and source event ID."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS episodes (
        episode_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL UNIQUE,
        subject_id TEXT NOT NULL,
        record_json TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_episodes_subject
        ON episodes(subject_id, created_at, episode_id);
    """

    def __init__(self, db_path: str | Path = "data/episodes.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(self.SCHEMA)
            self._conn.commit()
        except BaseException:
            self._conn.close()
            raise

    def append(self, episode: EpisodeRecord) -> bool:
        episode = read_episode(episode.as_record())
        encoded = json.dumps(
            episode.as_record(), ensure_ascii=False, separators=(",", ":")
        )
        with self._lock:
            existing = self._conn.execute(
                "SELECT episode_id, record_json FROM episodes WHERE event_id=? OR episode_id=?",
                (episode.event_id, episode.episode_id),
            ).fetchone()
            if existing is not None:
                if existing["record_json"] == encoded:
                    return False
                raise DuplicateEpisodeError(
                    "event_id or episode_id already belongs to another episode"
                )
            self._conn.execute(
                """INSERT INTO episodes (episode_id, event_id, subject_id, record_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    episode.episode_id,
                    episode.event_id,
                    episode.subject_id,
                    encoded,
                    time.time(),
                ),
            )
            self._conn.commit()
            return True

    def get(self, episode_id: str) -> EpisodeRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT record_json FROM episodes WHERE episode_id=?", (episode_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return read_episode(json.loads(row["record_json"]))
        except (ValueError, json.JSONDecodeError) as exc:
            raise EpisodeSchemaError("stored episode is invalid") from exc

    def get_by_event(self, event_id: str) -> EpisodeRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT record_json FROM episodes WHERE event_id=?", (event_id,)
            ).fetchone()
        if row is None:
            return None
        return read_episode(json.loads(row["record_json"]))

    def list_subject(self, subject_id: str, *, limit: int = 100) -> list[EpisodeRecord]:
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._lock:
            rows = self._conn.execute(
                """SELECT record_json FROM episodes WHERE subject_id=?
                   ORDER BY created_at, episode_id LIMIT ?""",
                (subject_id, limit),
            ).fetchall()
        return [read_episode(json.loads(row["record_json"])) for row in rows]

    def replay(self, episode_id: str) -> dict | None:
        episode = self.get(episode_id)
        return replay_projection(episode) if episode is not None else None

    def stats(self) -> dict[str, int]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            subjects = self._conn.execute(
                "SELECT COUNT(DISTINCT subject_id) FROM episodes"
            ).fetchone()[0]
        return {"episodes": int(total), "subjects": int(subjects)}

    def close(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.close()


__all__ = ["DuplicateEpisodeError", "EpisodeStore"]
