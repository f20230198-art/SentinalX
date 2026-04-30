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

-- Stage 4: local LLM (Mistral via Ollama) analyses.
--
-- llm_analyses: one row per raw_post. Holds the four sub-stage outputs of the
--               prompt chain — summary, intent, targets, techniques — plus the
--               raw JSON of each LLM response for debugging / reproducibility.
--
-- post_processing_state: per-stage cursor table. Keyed by (raw_post_id, stage)
--                        so Stages 4, 5, ... can each track their own progress
--                        without piling more columns onto raw_posts.
--
-- llm_runs: audit log mirroring scraper_runs / extraction_runs.

CREATE TABLE IF NOT EXISTS llm_analyses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_post_id     INTEGER NOT NULL UNIQUE,
    summary         TEXT,
    intent          TEXT,
    targets_json    TEXT,
    techniques_json TEXT,
    model           TEXT    NOT NULL,
    analysed_at     REAL    NOT NULL,
    raw_responses   TEXT,
    FOREIGN KEY(raw_post_id) REFERENCES raw_posts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_llm_analyses_post   ON llm_analyses(raw_post_id);
CREATE INDEX IF NOT EXISTS idx_llm_analyses_intent ON llm_analyses(intent);

CREATE TABLE IF NOT EXISTS post_processing_state (
    raw_post_id     INTEGER NOT NULL,
    stage           TEXT    NOT NULL,
    processed_at    REAL    NOT NULL,
    PRIMARY KEY (raw_post_id, stage),
    FOREIGN KEY(raw_post_id) REFERENCES raw_posts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pps_stage ON post_processing_state(stage);

CREATE TABLE IF NOT EXISTS llm_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      REAL    NOT NULL,
    finished_at     REAL,
    model           TEXT    NOT NULL,
    posts_seen      INTEGER NOT NULL DEFAULT 0,
    posts_completed INTEGER NOT NULL DEFAULT 0,
    posts_failed    INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);

-- Stage 5: MITRE ATT&CK ingest + vector index.
--
-- mitre_techniques: one row per Enterprise ATT&CK technique (and sub-technique).
--                   Embedding is stored as a raw float32 BLOB so we can mmap-load
--                   the whole matrix for cosine search without per-row JSON parse.
--
-- post_techniques: one row per (raw_post_id, technique_id, source). source is
--                  'llm_verified'   - LLM emitted this T-code AND it exists in corpus
--                  'llm_unverified' - LLM emitted this T-code but it's not in corpus
--                  'semantic'       - cosine search surfaced it; LLM didn't mention it
--                  Idempotent re-runs via UNIQUE(raw_post_id, technique_id, source).
--
-- mitre_runs: audit log mirroring scraper_runs / extraction_runs / llm_runs.

CREATE TABLE IF NOT EXISTS mitre_techniques (
    technique_id    TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    tactics         TEXT    NOT NULL,
    url             TEXT,
    is_subtechnique INTEGER NOT NULL DEFAULT 0,
    parent_id       TEXT,
    embedding       BLOB,
    embedding_model TEXT,
    ingested_at     REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mitre_parent ON mitre_techniques(parent_id);

CREATE TABLE IF NOT EXISTS post_techniques (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_post_id     INTEGER NOT NULL,
    technique_id    TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    score           REAL,
    evidence        TEXT,
    matched_at      REAL    NOT NULL,
    UNIQUE(raw_post_id, technique_id, source),
    FOREIGN KEY(raw_post_id) REFERENCES raw_posts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pt_post   ON post_techniques(raw_post_id);
CREATE INDEX IF NOT EXISTS idx_pt_tech   ON post_techniques(technique_id);
CREATE INDEX IF NOT EXISTS idx_pt_source ON post_techniques(source);

CREATE TABLE IF NOT EXISTS mitre_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          REAL    NOT NULL,
    finished_at         REAL,
    posts_seen          INTEGER NOT NULL DEFAULT 0,
    verified_inserted   INTEGER NOT NULL DEFAULT 0,
    unverified_inserted INTEGER NOT NULL DEFAULT 0,
    semantic_inserted   INTEGER NOT NULL DEFAULT 0,
    error               TEXT
);
