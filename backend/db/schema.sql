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
    fetched_at          REAL    NOT NULL,
    -- Which forum this post came from. 'darkbay' for the original JSON-API
    -- forum; an .onion host (or a label) for posts pulled by the generic HTML
    -- scraper. Defaults to 'darkbay' for any pre-existing rows on migration.
    source              TEXT    NOT NULL DEFAULT 'darkbay',
    -- Multilingual ingestion. Detected ISO 639-1 language of `body`
    -- ('en','ru','zh',…) or 'unknown' when undetectable. body_en holds the
    -- English translation when lang != 'en'; it is NULL for English posts (no
    -- translation needed). lang_confidence is langdetect's 0..1 probability.
    -- All three are filled by the extraction step before IOC/NER run.
    lang                TEXT,
    lang_confidence     REAL,
    body_en             TEXT
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

-- Extraction layer.
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

-- Local LLM (Mistral via Ollama) analyses.
--
-- llm_analyses: one row per raw_post. Holds the four sub-stage outputs of the
--               prompt chain — summary, intent, targets, techniques — plus the
--               raw JSON of each LLM response for debugging / reproducibility.
--
-- post_processing_state: per-stage cursor table. Keyed by (raw_post_id, stage)
--                        so each enrichment step (llm, mitre, …) can track its
--                        own progress without piling more columns onto raw_posts.
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

-- MITRE ATT&CK ingest + vector index.
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

-- MITRE ATT&CK mitigations (defensive recommendations).
--
-- The same Enterprise ATT&CK STIX file we already parse for techniques also
-- contains 'course-of-action' objects (the official mitigations, Mxxxx codes)
-- and 'relationship' objects of type 'mitigates' linking a course-of-action to
-- an attack-pattern. We parse those here so every technique on a post can carry
-- MITRE's own recommended countermeasures — no LLM, no guessing, pure lookup.
--
-- mitre_mitigations:     one row per Enterprise mitigation (Mxxxx).
-- technique_mitigations: join table, one row per (technique, mitigation) edge.
--                        Idempotent re-ingest via UNIQUE(technique_id, mitigation_id).

CREATE TABLE IF NOT EXISTS mitre_mitigations (
    mitigation_id   TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    url             TEXT,
    ingested_at     REAL    NOT NULL
);

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

-- Investigations layer.
--
-- An investigation is a named, replayable view: a saved filter over the corpus
-- plus an optional cross-post LLM summary written through one of four "lenses"
-- (threat_intel / ransomware / personal_identity / corporate_espionage). The
-- filter is stored as JSON and re-evaluated on every read, so investigations
-- stay in sync as new posts arrive — they aren't frozen snapshots.
--
-- A rerun re-evaluates the filter, picks the matching post bodies + their
-- enrichment, and asks the LLM to write a single fused summary under the
-- chosen lens. The result is stored on the investigation row; reruns
-- overwrite. We don't keep history (no `investigation_summaries` table)
-- because storage cost > evidence value for a learning project.
--
-- Lens names live in code (backend/llm/lenses.py), not in the DB, so we can
-- evolve prompts without a migration. We just store the lens name string.

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

-- On-demand pipeline jobs.
--
-- A pipeline job is one end-to-end run triggered from the UI by pasting an
-- .onion URL: scrape (HTML) -> extract -> LLM -> MITRE -> mitigations. The job
-- runs in a background thread; this row is its live status, polled by the
-- frontend. `stage` is the human-readable current step; `status` is the
-- lifecycle state (queued | running | done | error). Counts accumulate as
-- stages complete so the UI can show progress without parsing logs.
--
-- This is observability state, not pipeline data — the actual posts/iocs/etc.
-- land in their normal tables, tagged with the job's `source`.

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
