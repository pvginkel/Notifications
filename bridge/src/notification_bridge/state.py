"""The bridge's state: one SQLite table of what an update or a dismiss needs, never content.

A row is one channel's hold on one open sequence of a registration. ntfy keeps one row per
sequence: the priority and the generation its notification is published under. A row holds no
title and no message text.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from notification_bridge.render import Priority

# A row untouched for longer is deleted by the prune.
RETENTION = timedelta(days=90)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sequence (
    registration TEXT NOT NULL,
    sequence_id TEXT NOT NULL,
    channel TEXT NOT NULL,      -- 'ntfy' or 'telegram'
    chat_id TEXT NOT NULL,      -- the Telegram chat; '' on ntfy's row
    message_id INTEGER,         -- the Telegram message; NULL on ntfy's row
    generation INTEGER,         -- the ntfy generation; NULL on a Telegram row
    priority INTEGER NOT NULL,
    last_updated TEXT NOT NULL, -- UTC, ISO 8601 to the second: its text order is its time order
    PRIMARY KEY (registration, sequence_id, channel, chat_id)
)
"""


@dataclass(frozen=True)
class NtfyRow:
    priority: Priority
    generation: int


class State:
    def __init__(self, path: Path, now: Callable[[], datetime]) -> None:
        # The event loop's thread is the only one that uses the connection, and it need not be the
        # thread that opens it.
        self._db = sqlite3.connect(path, autocommit=True, check_same_thread=False)
        self._db.execute(SCHEMA)
        self._now = now

    def close(self) -> None:
        self._db.close()

    def ntfy_row(self, registration: str, sequence_id: str) -> NtfyRow | None:
        row = self._db.execute(
            "SELECT priority, generation FROM sequence"
            " WHERE registration = ? AND sequence_id = ? AND channel = 'ntfy'",
            (registration, sequence_id),
        ).fetchone()
        return None if row is None else NtfyRow(Priority(row[0]), row[1])

    def put_ntfy_row(self, registration: str, sequence_id: str, row: NtfyRow) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO sequence"
            " (registration, sequence_id, channel, chat_id, generation, priority, last_updated)"
            " VALUES (?, ?, 'ntfy', '', ?, ?, ?)",
            (registration, sequence_id, row.generation, row.priority.value, self._timestamp()),
        )

    def delete_ntfy_row(self, registration: str, sequence_id: str) -> None:
        self._db.execute(
            "DELETE FROM sequence WHERE registration = ? AND sequence_id = ? AND channel = 'ntfy'",
            (registration, sequence_id),
        )

    def prune(self) -> int:
        """Delete the rows last updated longer than `RETENTION` ago, and count them."""
        cutoff = _timestamp(self._now() - RETENTION)
        return self._db.execute("DELETE FROM sequence WHERE last_updated < ?", (cutoff,)).rowcount

    def open_rows(self) -> dict[tuple[str, str], int]:
        """The number of rows per registration and channel."""
        rows = self._db.execute(
            "SELECT registration, channel, COUNT(*) FROM sequence GROUP BY registration, channel"
        )
        return {(registration, channel): count for registration, channel, count in rows}

    def _timestamp(self) -> str:
        return _timestamp(self._now())


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")
