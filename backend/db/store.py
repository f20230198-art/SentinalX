"""SQLite store: saves scraped posts and scraper run logs."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

# Paths for the schema file and the database file
HERE = Path(__file__).parent.resolve()
SCHEMA_PATH = HERE / "schema.sql"

DEFAULT_DB_PATH = HERE / "sentinelx.db"


# Wrapper around the SQLite database used by the scraper and pipeline stages
class Store:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Open the DB; rows act like dicts (row["body"])
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        # Wait for locks instead of failing when the API and a job use the DB together
        self.conn.execute("PRAGMA busy_timeout = 5000")
        # Create any missing tables/columns
        self._init_schema()

    def _init_schema(self) -> None:
        # Create all tables from schema.sql (skips ones that exist)
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            self.conn.executescript(f.read())
        # Add newer columns to old DBs (skip ones that already exist)
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(raw_posts)")}
        if "processed_at" not in cols:
            self.conn.execute("ALTER TABLE raw_posts ADD COLUMN processed_at REAL")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_raw_posts_processed ON raw_posts(processed_at)"
            )
        if "source" not in cols:
            # Pre-existing rows were all scraped from the original JSON forum.
            self.conn.execute(
                "ALTER TABLE raw_posts ADD COLUMN source TEXT NOT NULL DEFAULT 'darkbay'"
            )
        # Language columns (filled in by the extraction step)
        if "lang" not in cols:
            self.conn.execute("ALTER TABLE raw_posts ADD COLUMN lang TEXT")
        if "lang_confidence" not in cols:
            self.conn.execute("ALTER TABLE raw_posts ADD COLUMN lang_confidence REAL")
        if "body_en" not in cols:
            self.conn.execute("ALTER TABLE raw_posts ADD COLUMN body_en TEXT")
        # Run upgrades, then make sure the indexes exist
        self._migrate_composite_post_key()
        self._ensure_search_index()
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_posts_processed ON raw_posts(processed_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_posts_source ON raw_posts(source)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_posts_lang ON raw_posts(lang)"
        )
        self.conn.commit()

    def _migrate_composite_post_key(self) -> None:
        """Migrate old DBs: make posts unique per (source, post id) instead of globally."""
        # Already migrated? Nothing to do
        sql = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='raw_posts'"
        ).fetchone()["sql"]
        if "UNIQUE(source, source_post_id)" in sql.replace("\n", " "):
            return
        # Rebuild the table with the new rule: create new, copy rows, drop old, rename
        cols = [r["name"] for r in self.conn.execute("PRAGMA table_info(raw_posts)")]
        col_list = ", ".join(cols)
        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys = OFF")
        try:
            self.conn.executescript(f"""
                BEGIN;
                CREATE TABLE raw_posts_new (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_post_id      INTEGER NOT NULL,
                    source_thread_id    INTEGER NOT NULL,
                    thread_title        TEXT    NOT NULL,
                    category            TEXT    NOT NULL,
                    author              TEXT    NOT NULL,
                    body                TEXT    NOT NULL,
                    source_created_at   REAL    NOT NULL,
                    fetched_at          REAL    NOT NULL,
                    source              TEXT    NOT NULL DEFAULT 'darkbay',
                    lang                TEXT,
                    lang_confidence     REAL,
                    body_en             TEXT,
                    processed_at        REAL,
                    UNIQUE(source, source_post_id)
                );
                INSERT INTO raw_posts_new ({col_list}) SELECT {col_list} FROM raw_posts;
                DROP TABLE raw_posts;
                ALTER TABLE raw_posts_new RENAME TO raw_posts;
                CREATE INDEX IF NOT EXISTS idx_raw_posts_source_created ON raw_posts(source_created_at);
                CREATE INDEX IF NOT EXISTS idx_raw_posts_thread         ON raw_posts(source_thread_id);
                CREATE INDEX IF NOT EXISTS idx_raw_posts_category       ON raw_posts(category);
                COMMIT;
            """)
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")

    def _ensure_search_index(self) -> None:
        """Full-text search index on post titles/bodies, kept in sync by triggers."""
        # The search index + triggers that update it on insert/delete/update
        self.conn.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
                thread_title, body, body_en,
                content='raw_posts', content_rowid='id',
                tokenize='porter unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS raw_posts_ai AFTER INSERT ON raw_posts BEGIN
                INSERT INTO posts_fts(rowid, thread_title, body, body_en)
                VALUES (new.id, new.thread_title, new.body, new.body_en);
            END;
            CREATE TRIGGER IF NOT EXISTS raw_posts_ad AFTER DELETE ON raw_posts BEGIN
                INSERT INTO posts_fts(posts_fts, rowid, thread_title, body, body_en)
                VALUES ('delete', old.id, old.thread_title, old.body, old.body_en);
            END;
            CREATE TRIGGER IF NOT EXISTS raw_posts_au AFTER UPDATE OF thread_title, body, body_en ON raw_posts BEGIN
                INSERT INTO posts_fts(posts_fts, rowid, thread_title, body, body_en)
                VALUES ('delete', old.id, old.thread_title, old.body, old.body_en);
                INSERT INTO posts_fts(rowid, thread_title, body, body_en)
                VALUES (new.id, new.thread_title, new.body, new.body_en);
            END;
        """)
        # If the index is missing posts, rebuild it
        indexed = self.conn.execute("SELECT COUNT(*) FROM posts_fts_docsize").fetchone()[0]
        total = self.conn.execute("SELECT COUNT(*) FROM raw_posts").fetchone()[0]
        if indexed != total:
            # First run (or index out of step): rebuild from raw_posts.
            self.conn.execute("INSERT INTO posts_fts(posts_fts) VALUES ('rebuild')")

    def close(self) -> None:
        self.conn.close()

    # --- cursor ----------------------------------------------------------- #

    def get_cursor(self, source: str | None = None) -> float:
        """Latest post timestamp (optionally for one forum), or 0.0 if empty."""
        if source is not None:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(source_created_at), 0.0) AS c "
                "FROM raw_posts WHERE source = ?",
                (source,),
            ).fetchone()
        else:
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

    def insert_posts(self, posts: Iterable[dict], source: str = "darkbay") -> tuple[int, int]:
        """Insert posts, skipping duplicates; returns (inserted, duplicates)."""
        inserted = 0
        duplicates = 0
        fetched_at = time.time()

        # Insert each post; a duplicate raises IntegrityError and is counted instead
        for p in posts:
            try:
                self.conn.execute(
                    """
                    INSERT INTO raw_posts (
                        source_post_id, source_thread_id, thread_title,
                        category, author, body, source_created_at, fetched_at,
                        source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        p.get("source", source),
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                # UNIQUE(source, source_post_id) — already have it.
                duplicates += 1

        self.conn.commit()
        return inserted, duplicates

    # --- run log ---------------------------------------------------------- #

    # Context manager: logs a scraper run start, then records results (or the error) at the end
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

    # Total number of posts stored
    def count_posts(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM raw_posts").fetchone()["n"])


# Holds the counts for one scraper run and writes them to scraper_runs
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

    # Write the final counts to the run's row (only once)
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
