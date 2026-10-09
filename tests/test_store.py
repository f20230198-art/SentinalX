"""Store: composite post key, per-source cursor, idempotent inserts, migration."""

import sqlite3

from backend.db.store import Store


# Helper: build a fake post dict
def post(pid: int, ts: float, **kw) -> dict:
    return {"id": pid, "thread_id": 1, "thread_title": "t", "category": "c",
            "author": "a", "body": "b", "created_at": ts, **kw}


def test_same_post_id_on_two_forums_does_not_collide(tmp_path):
    s = Store(tmp_path / "x.db")
    assert s.insert_posts([post(30, 1.0)], source="darkbay") == (1, 0)
    assert s.insert_posts([post(30, 1.0)], source="silkvault") == (1, 0)
    assert s.count_posts() == 2


def test_reinserting_is_idempotent(tmp_path):
    s = Store(tmp_path / "x.db")
    s.insert_posts([post(1, 1.0), post(2, 2.0)], source="darkbay")
    assert s.insert_posts([post(1, 1.0), post(2, 2.0)], source="darkbay") == (0, 2)
    assert s.count_posts() == 2


def test_cursor_is_scoped_per_source(tmp_path):
    s = Store(tmp_path / "x.db")
    s.insert_posts([post(1, 100.0)], source="darkbay")
    s.insert_posts([post(1, 50.0)], source="silkvault")
    assert s.get_cursor(source="darkbay") == 100.0
    # Regression: SilkVault used to use the global cursor and skip its posts
    assert s.get_cursor(source="silkvault") == 50.0
    assert s.get_cursor() == 100.0


def test_migrates_old_global_unique_schema(tmp_path):
    """Old DB with global UNIQUE(source_post_id) is migrated, keeping row ids."""
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE raw_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_post_id INTEGER NOT NULL UNIQUE,
            source_thread_id INTEGER NOT NULL, thread_title TEXT NOT NULL,
            category TEXT NOT NULL, author TEXT NOT NULL, body TEXT NOT NULL,
            source_created_at REAL NOT NULL, fetched_at REAL NOT NULL);
        INSERT INTO raw_posts VALUES (7, 30, 1, 't', 'c', 'a', 'b', 1.0, 1.0);
        CREATE TABLE iocs (id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_post_id INTEGER NOT NULL, ioc_type TEXT NOT NULL, value TEXT NOT NULL,
            span_start INTEGER, span_end INTEGER, extracted_at REAL NOT NULL,
            UNIQUE(raw_post_id, ioc_type, value),
            FOREIGN KEY(raw_post_id) REFERENCES raw_posts(id) ON DELETE CASCADE);
        INSERT INTO iocs (raw_post_id, ioc_type, value, extracted_at) VALUES (7, 'ipv4', '1.2.3.4', 1.0);
    """)
    c.commit()
    c.close()

    s = Store(db)
    row = s.conn.execute("SELECT id, source FROM raw_posts").fetchone()
    assert (row["id"], row["source"]) == (7, "darkbay")
    assert s.conn.execute(
        "SELECT COUNT(*) FROM iocs i JOIN raw_posts r ON r.id = i.raw_post_id"
    ).fetchone()[0] == 1
    assert s.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # And the new rule is in force.
    assert s.insert_posts([post(30, 1.0)], source="silkvault") == (1, 0)
    # Re-opening is a no-op (migration is idempotent).
    s.close()
    assert Store(db).count_posts() == 2
