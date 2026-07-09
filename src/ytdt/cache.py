"""Persistent cache for API facts that never change.

One fact is cached so far: a video's Shorts status, which is fixed at
upload but slow to determine (see
:func:`~ytdt.modules.video_list.detect_shorts`). Timely data — view
counts, comments, search results — is deliberately never cached.

The cache is a small SQLite file shared by all runs (and safe to share
between processes); delete it at any time to start fresh.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .utils import chunked


def default_cache_path() -> Path:
    """``$YTDT_CACHE_DIR/cache.db``, defaulting to ``~/.ytdt/cache.db``."""
    root = Path(os.environ.get("YTDT_CACHE_DIR") or Path.home() / ".ytdt")
    return root / "cache.db"


class FactCache:
    """SQLite-backed ``video_id -> isShort`` store, usable across threads."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_cache_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS shorts ("
            "video_id TEXT PRIMARY KEY, is_short TEXT NOT NULL, stored_at TEXT NOT NULL)"
        )
        self._conn.commit()

    def get_shorts(self, video_ids: list[str]) -> dict[str, str]:
        """Cached ``video_id -> "yes"/"no"`` for the ids that are known."""
        known: dict[str, str] = {}
        with self._lock:
            for batch in chunked(video_ids, 500):
                marks = ",".join("?" * len(batch))
                rows = self._conn.execute(
                    f"SELECT video_id, is_short FROM shorts WHERE video_id IN ({marks})",
                    list(batch),
                )
                known.update(rows.fetchall())
        return known

    def put_shorts(self, statuses: dict[str, str]) -> None:
        if not statuses:
            return
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO shorts VALUES (?, ?, ?)",
                [(vid, status, now) for vid, status in statuses.items()],
            )
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()
