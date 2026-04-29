-- Synthetic darknet forum schema.
-- Two tables only — threads and the posts that hang off them.
-- Timestamps are stored as Unix epoch floats so the JSON API can do simple
-- numeric `since` filtering without date parsing.

CREATE TABLE IF NOT EXISTS threads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    author      TEXT    NOT NULL,
    created_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   INTEGER NOT NULL,
    author      TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    created_at  REAL    NOT NULL,
    FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_posts_thread    ON posts(thread_id);
CREATE INDEX IF NOT EXISTS idx_posts_created   ON posts(created_at);
CREATE INDEX IF NOT EXISTS idx_threads_category ON threads(category);
