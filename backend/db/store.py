"""SQLite-backed store for raw scraped posts and scraper run metadata.

Single connection per Store instance. The scraper is a single writer so we
don't need WAL or per-thread connections; we do enable foreign keys for
consistency with the forum schema.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

HERE = Path(__file__).parent.resolve()
SCHEMA_PATH = HERE / "schema.sql"

DEFAULT_DB_PATH = HERE / "sentinelx.db"


class Store:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            self.conn.executescript(f.read())
        # processed_at was added in Stage 3. Add it idempotently for DBs that
        # were created before then. SQLite has no IF NOT EXISTS for ADD COLUMN.
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(raw_posts)")}
        if "processed_at" not in cols:
            self.conn.execute("ALTER TABLE raw_posts ADD COLUMN processed_at REAL")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_raw_posts_processed ON raw_posts(processed_at)"
            )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- cursor ----------------------------------------------------------- #

    def get_cursor(self) -> float:
        """Return MAX(source_created_at) over raw_posts, or 0.0 if empty.

        Using MAX over the data instead of a separate state row means the cursor
        cannot drift out of sync with what's actually stored.
        """
        row = self.conn.execute(
            "SELECT COALESCE(MAX(source_created_at), 0.0) AS c FROM raw_posts"
        ).fetchone()
        return float(row["c"])

    def reset(self) -> None:
        """Drop all raw_posts and scraper_runs. Used by --reset-cursor."""
        self.conn.executescript(
            "DELETE FROM raw_posts; DELETE FROM scraper_runs;"
            "DELETE FROM sqlite_sequence WHERE name IN ('raw_posts','scraper_runs');"
        )
        self.conn.commit()

    # --- inserts ---------------------------------------------------------- #

    def insert_posts(self, posts: Iterable[dict]) -> tuple[int, int]:
        """Insert posts, ignoring duplicates by source_post_id.

        Returns (inserted, duplicates).
        """
        inserted = 0
        duplicates = 0
        fetched_at = time.time()

        for p in posts:
            try:
                self.conn.execute(
                    """
                    INSERT INTO raw_posts (
                        source_post_id, source_thread_id, thread_title,
                        category, author, body, source_created_at, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(p["id"]),
                        int(p["thread_id"]),
                        p["thread_title"],
                        p["category"],
                        p["author"],
                        p["body"],
                        float(p["created_at"]),
                        fetched_at,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                # UNIQUE constraint on source_post_id — already have it.
                duplicates += 1

        self.conn.commit()
        return inserted, duplicates

    # --- run log ---------------------------------------------------------- #

    @contextmanager
    def run(self, cursor_before: float) -> Iterator["RunHandle"]:
        started = time.time()
        cur = self.conn.execute(
            "INSERT INTO scraper_runs (started_at, cursor_before) VALUES (?, ?)",
            (started, cursor_before),
        )
        run_id = cur.lastrowid
        self.conn.commit()
        handle = RunHandle(self.conn, run_id)
        try:
            yield handle
        except Exception as e:
            handle.error = repr(e)
            handle.finalize()
            raise
        else:
            handle.finalize()

    # --- diagnostics ------------------------------------------------------ #

    def count_posts(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM raw_posts").fetchone()["n"])


class RunHandle:
    def __init__(self, conn: sqlite3.Connection, run_id: int) -> None:
        self.conn = conn
        self.run_id = run_id
        self.fetched = 0
        self.inserted = 0
        self.duplicates = 0
        self.cursor_after: float | None = None
        self.error: str | None = None
        self._finalized = False

    def finalize(self) -> None:
        if self._finalized:
            return
        self.conn.execute(
            """
            UPDATE scraper_runs
            SET finished_at = ?, cursor_after = ?, fetched = ?,
                inserted = ?, duplicates = ?, error = ?
            WHERE id = ?
            """,
            (
                time.time(),
                self.cursor_after,
                self.fetched,
                self.inserted,
                self.duplicates,
                self.error,
                self.run_id,
            ),
        )
        self.conn.commit()
        self._finalized = True
