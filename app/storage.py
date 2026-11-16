from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class NoteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    note_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_attempt_at TEXT,
                    tweet_ids_json TEXT NOT NULL DEFAULT '[]',
                    media_ids_json TEXT NOT NULL DEFAULT '{}',
                    error_reason TEXT,
                    source_url TEXT,
                    baseline INTEGER NOT NULL DEFAULT 0,
                    tweet_id TEXT
                )
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(notes)").fetchall()
            }
            if "tweet_id" not in columns:
                connection.execute("ALTER TABLE notes ADD COLUMN tweet_id TEXT")

    def is_empty(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM notes LIMIT 1").fetchone() is None

    def baseline_initialized(self) -> bool:
        with self._connect() as connection:
            return (
                connection.execute("SELECT 1 FROM notes WHERE baseline=1 LIMIT 1").fetchone()
                is not None
            )

    def upsert_discovered(self, note_id: str, source_url: str, baseline: bool = False) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO notes(note_id,status,first_seen_at,source_url,baseline)
                VALUES(?,?,?,?,?)
                ON CONFLICT(note_id) DO UPDATE SET source_url=COALESCE(excluded.source_url, notes.source_url)
                """,
                (
                    note_id,
                    "baseline" if baseline else "discovered",
                    now_iso(),
                    source_url,
                    int(baseline),
                ),
            )

    def get(self, note_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("SELECT * FROM notes WHERE note_id=?", (note_id,)).fetchone()

    def set_status(
        self,
        note_id: str,
        status: str,
        error_reason: str | None = None,
        tweet_ids: list[str] | None = None,
        media_ids: dict[str, list[str]] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE notes SET status=?, last_attempt_at=?, error_reason=?,
                    tweet_ids_json=COALESCE(?, tweet_ids_json), media_ids_json=COALESCE(?, media_ids_json),
                    tweet_id=COALESCE(?, tweet_id)
                WHERE note_id=?
                """,
                (
                    status,
                    now_iso(),
                    error_reason,
                    json.dumps(tweet_ids) if tweet_ids is not None else None,
                    json.dumps(media_ids) if media_ids is not None else None,
                    tweet_ids[0] if tweet_ids and tweet_ids[0] else None,
                    note_id,
                ),
            )

    def mark_baseline(self, note_ids: list[str]) -> None:
        with self._connect() as connection:
            for note_id in note_ids:
                connection.execute(
                    "UPDATE notes SET status='baseline', baseline=1 WHERE note_id=?",
                    (note_id,),
                )

    def get_publish_state(self, note_id: str) -> tuple[list[str], dict[str, list[str]]]:
        row = self.get(note_id)
        if not row:
            return [], {}
        return json.loads(row["tweet_ids_json"]), json.loads(row["media_ids_json"])
