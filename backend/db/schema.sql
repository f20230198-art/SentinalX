-- SentinelX database. raw_posts = scraped posts; scraper_runs = one log row per scrape.

-- Every scraped post, exactly as it was on the forum
CREATE TABLE IF NOT EXISTS raw_posts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Post id from the forum (unique per forum, not globally)
    source_post_id      INTEGER NOT NULL,
    source_thread_id    INTEGER NOT NULL,
    thread_title        TEXT    NOT NULL,
    category            TEXT    NOT NULL,
    author              TEXT    NOT NULL,
    body                TEXT    NOT NULL,
    source_created_at   REAL    NOT NULL,
    fetched_at          REAL    NOT NULL,
    -- Which forum the post came from
    source              TEXT    NOT NULL DEFAULT 'darkbay',
    -- Detected language, its confidence, and English translation (NULL if already English)
    lang                TEXT,
    lang_confidence     REAL,
    body_en             TEXT,
    processed_at        REAL,
    UNIQUE(source, source_post_id)
);

-- Indexes = faster lookups by time, thread and category
CREATE INDEX IF NOT EXISTS idx_raw_posts_source_created ON raw_posts(source_created_at);
CREATE INDEX IF NOT EXISTS idx_raw_posts_thread         ON raw_posts(source_thread_id);
CREATE INDEX IF NOT EXISTS idx_raw_posts_category       ON raw_posts(category);

-- One row per scraper run: when, cursor before/after, and counts
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

-- Extraction: iocs + entities found in each post, extraction_runs = run log.

-- IOCs found in each post (IPs, hashes, CVEs, wallets, ...)
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

-- Named entities found in each post (orgs, people, malware, actors, ...)
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

-- One row per extraction run
CREATE TABLE IF NOT EXISTS extraction_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      REAL    NOT NULL,
    finished_at     REAL,
    posts_seen      INTEGER NOT NULL DEFAULT 0,
    iocs_inserted   INTEGER NOT NULL DEFAULT 0,
    entities_inserted INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);

-- LLM analysis: llm_analyses = per-post results, post_processing_state = which stage each post finished, llm_runs = run log.

-- LLM results per post: summary, intent, targets, suggested techniques
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

-- Which pipeline stages ('llm', 'mitre') each post has finished
CREATE TABLE IF NOT EXISTS post_processing_state (
    raw_post_id     INTEGER NOT NULL,
    stage           TEXT    NOT NULL,
    processed_at    REAL    NOT NULL,
    PRIMARY KEY (raw_post_id, stage),
    FOREIGN KEY(raw_post_id) REFERENCES raw_posts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pps_stage ON post_processing_state(stage);

-- One row per LLM run
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

-- MITRE: mitre_techniques (with embeddings), post_techniques (source = llm_verified | llm_unverified | semantic), mitre_runs = run log.

-- The official MITRE technique list, each with its embedding vector
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

-- Which techniques each post was matched to, and how (source + score)
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

-- One row per MITRE matching run
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

-- MITRE mitigations (Mxxxx) and which techniques each one mitigates.

-- Official MITRE mitigations (defences)
CREATE TABLE IF NOT EXISTS mitre_mitigations (
    mitigation_id   TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    url             TEXT,
    ingested_at     REAL    NOT NULL
);

-- Which mitigation helps against which technique
CREATE TABLE IF NOT EXISTS technique_mitigations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    technique_id    TEXT    NOT NULL,
    mitigation_id   TEXT    NOT NULL,
    ingested_at     REAL    NOT NULL,
    UNIQUE(technique_id, mitigation_id),
    FOREIGN KEY(technique_id)  REFERENCES mitre_techniques(technique_id)   ON DELETE CASCADE,
    FOREIGN KEY(mitigation_id) REFERENCES mitre_mitigations(mitigation_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tm_technique  ON technique_mitigations(technique_id);
CREATE INDEX IF NOT EXISTS idx_tm_mitigation ON technique_mitigations(mitigation_id);

-- Investigations: saved filter (re-run on every read) + optional LLM lens summary.

-- Saved investigations: name, filters (JSON), lens, latest LLM summary
CREATE TABLE IF NOT EXISTS investigations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    description     TEXT,
    filters_json    TEXT    NOT NULL,
    lens            TEXT,
    summary         TEXT,
    summary_model   TEXT,
    summary_post_ids TEXT,
    created_at      REAL    NOT NULL,
    updated_at      REAL    NOT NULL,
    last_run_at     REAL
);

CREATE INDEX IF NOT EXISTS idx_investigations_created ON investigations(created_at DESC);

-- Pipeline jobs started from the UI (Scout); live status polled by the frontend.

-- Scrape jobs: live status, current stage, counts, and errors
CREATE TABLE IF NOT EXISTS pipeline_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    onion_url       TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'queued',
    stage           TEXT    NOT NULL DEFAULT 'queued',
    posts_scraped   INTEGER NOT NULL DEFAULT 0,
    posts_extracted INTEGER NOT NULL DEFAULT 0,
    posts_llm       INTEGER NOT NULL DEFAULT 0,
    techniques_mapped INTEGER NOT NULL DEFAULT 0,
    llm_skipped     INTEGER NOT NULL DEFAULT 0,
    message         TEXT,
    error           TEXT,
    created_at      REAL    NOT NULL,
    updated_at      REAL    NOT NULL,
    finished_at     REAL
);

CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_created ON pipeline_jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_status  ON pipeline_jobs(status);

-- Watchlists (saved terms) and watch_hits (posts that mention them = alerts).

-- Watchlists: a name + list of terms; last_post_id = how far we've checked
CREATE TABLE IF NOT EXISTS watchlists (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    terms_json      TEXT    NOT NULL,
    created_at      REAL    NOT NULL,
    last_post_id    INTEGER NOT NULL DEFAULT 0
);

-- Alerts: a post that mentions a watchlist term (seen = 0 means new)
CREATE TABLE IF NOT EXISTS watch_hits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    watchlist_id    INTEGER NOT NULL,
    raw_post_id     INTEGER NOT NULL,
    matched_terms   TEXT    NOT NULL,
    created_at      REAL    NOT NULL,
    seen            INTEGER NOT NULL DEFAULT 0,
    UNIQUE(watchlist_id, raw_post_id),
    FOREIGN KEY(watchlist_id) REFERENCES watchlists(id) ON DELETE CASCADE,
    FOREIGN KEY(raw_post_id)  REFERENCES raw_posts(id)  ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_watch_hits_unseen ON watch_hits(seen, created_at DESC);
