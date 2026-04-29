-- SentinelX backend store. Stages 2+ write here.
--
-- raw_posts: every post the scraper pulls from the forum, deduplicated by
--            source_post_id. We keep the source's epoch timestamp verbatim so
--            the scraper can use MAX(source_created_at) as its incremental
--            cursor without a separate state table.
--
-- scraper_runs: one row per scraper invocation. Useful for debugging "did the
--               last poll see anything new?" and for observability later.

CREATE TABLE IF NOT EXISTS raw_posts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_post_id      INTEGER NOT NULL UNIQUE,
    source_thread_id    INTEGER NOT NULL,
    thread_title        TEXT    NOT NULL,
    category            TEXT    NOT NULL,
    author              TEXT    NOT NULL,
    body                TEXT    NOT NULL,
    source_created_at   REAL    NOT NULL,
    fetched_at          REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_posts_source_created ON raw_posts(source_created_at);
CREATE INDEX IF NOT EXISTS idx_raw_posts_thread         ON raw_posts(source_thread_id);
CREATE INDEX IF NOT EXISTS idx_raw_posts_category       ON raw_posts(category);

CREATE TABLE IF NOT EXISTS scraper_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      REAL    NOT NULL,
    finished_at     REAL,
    cursor_before   REAL    NOT NULL,
    cursor_after    REAL,
    fetched         INTEGER NOT NULL DEFAULT 0,
    inserted        INTEGER NOT NULL DEFAULT 0,
    duplicates      INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);

-- Stage 3: extraction layer.
--
-- iocs: one row per (post, type, value). Indicators of Compromise pulled out
--       of post bodies via regex over a defanged-then-normalised copy of the
--       text. Re-extracting the same post is idempotent (UNIQUE constraint).
--
-- entities: one row per (post, label, text). Named entities from spaCy's
--           en_core_web_sm plus a curated malware/threat-actor pass.
--
-- extraction_runs: audit log, mirrors scraper_runs.

CREATE TABLE IF NOT EXISTS iocs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_post_id     INTEGER NOT NULL,
    ioc_type        TEXT    NOT NULL,
    value           TEXT    NOT NULL,
    span_start      INTEGER,
    span_end        INTEGER,
    extracted_at    REAL    NOT NULL,
    UNIQUE(raw_post_id, ioc_type, value),
    FOREIGN KEY(raw_post_id) REFERENCES raw_posts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_iocs_post ON iocs(raw_post_id);
CREATE INDEX IF NOT EXISTS idx_iocs_type ON iocs(ioc_type);
CREATE INDEX IF NOT EXISTS idx_iocs_value ON iocs(value);

CREATE TABLE IF NOT EXISTS entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_post_id     INTEGER NOT NULL,
    label           TEXT    NOT NULL,
    text            TEXT    NOT NULL,
    span_start      INTEGER,
    span_end        INTEGER,
    extracted_at    REAL    NOT NULL,
    UNIQUE(raw_post_id, label, text),
    FOREIGN KEY(raw_post_id) REFERENCES raw_posts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_entities_post  ON entities(raw_post_id);
CREATE INDEX IF NOT EXISTS idx_entities_label ON entities(label);

CREATE TABLE IF NOT EXISTS extraction_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      REAL    NOT NULL,
    finished_at     REAL,
    posts_seen      INTEGER NOT NULL DEFAULT 0,
    iocs_inserted   INTEGER NOT NULL DEFAULT 0,
    entities_inserted INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);
