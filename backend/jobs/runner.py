"""On-demand pipeline job runner.

A pipeline job is one end-to-end run triggered from the UI: the user pastes an
.onion URL, and SentinelX scrapes it (HTML), extracts IOCs/entities, enriches
with the LLM, maps to MITRE ATT&CK, and surfaces mitigations — all on demand,
for a forum it has never seen before.

The runner executes every stage **in-process** by calling the existing stage
functions directly (no subprocesses): it is the same code paths as
`python -m backend.scraper.run` etc., just driven from one function and
reporting progress into the `pipeline_jobs` row as it goes.

Concurrency model:
  * One job runs in its own background thread (started by the API layer).
  * The job opens its OWN Store / sqlite connection — never shares the API's
    connection. SQLite serialises writes, and each stage is a short burst, so
    a single concurrent job is safe. The API only ever READS pipeline_jobs.

Graceful LLM degradation:
  * The LLM stage needs Ollama. If Ollama is unreachable, the job does NOT
    fail — it skips LLM enrichment, records `llm_skipped`, and still runs
    extraction + semantic MITRE matching (which uses MiniLM embeddings, not
    the LLM). A forum still gets IOCs and technique mappings without a GPU.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path

from backend.db.store import Store

log = logging.getLogger("sentinelx.jobs")


# --------------------------------------------------------------------------- #
# Job-row helpers — every mutation goes through here so updated_at stays fresh.
# --------------------------------------------------------------------------- #

def create_job(conn: sqlite3.Connection, onion_url: str, source: str) -> int:
    """Insert a queued job row and return its id."""
    now = time.time()
    cur = conn.execute(
        "INSERT INTO pipeline_jobs (onion_url, source, status, stage, "
        "  created_at, updated_at) VALUES (?, ?, 'queued', 'queued', ?, ?)",
        (onion_url, source, now, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM pipeline_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    return dict(row) if row else None


def list_jobs(conn: sqlite3.Connection, limit: int = 25) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM pipeline_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def _update(conn: sqlite3.Connection, job_id: int, **fields) -> None:
    """Patch named columns on a job row; always bumps updated_at."""
    fields["updated_at"] = time.time()
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE pipeline_jobs SET {sets} WHERE id = ?",
        (*fields.values(), job_id),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# The job itself.
# --------------------------------------------------------------------------- #

def run_job(
    job_id: int,
    onion_url: str,
    source: str,
    db_path: str | Path | None = None,
    proxy: str = "socks5://127.0.0.1:9051",
    ollama_url: str = "http://127.0.0.1:11434",
    skip_llm: bool = False,
) -> None:
    """Run the full pipeline for one .onion URL. Designed to run in a thread.

    `skip_llm` forces the LLM enrichment stage to be skipped — the "fast mode"
    used for a live demo on battery, where Mistral is too slow to wait for.
    Scrape + extract + semantic MITRE still run, so the forum still gets IOCs
    and ATT&CK mappings; only the LLM summary/intent fields are left empty.

    Updates the pipeline_jobs row as each stage completes. Never raises — any
    failure is captured into the row's `error` field and `status='error'`, so
    the caller (a daemon thread) can't crash silently.
    """
    store = Store(db_path) if db_path else Store()
    conn = store.conn
    try:
        _run_stages(conn, store, job_id, onion_url, source, proxy, ollama_url,
                    skip_llm)
    except Exception as e:  # noqa: BLE001 — top-level guard for the worker thread
        log.exception("job %d failed", job_id)
        _update(conn, job_id, status="error", stage="error",
                error=repr(e), finished_at=time.time())
    finally:
        store.close()


def _run_stages(
    conn: sqlite3.Connection,
    store: Store,
    job_id: int,
    onion_url: str,
    source: str,
    proxy: str,
    ollama_url: str,
    skip_llm: bool,
) -> None:
    # --- Stage 1: scrape (HTML) --------------------------------------- #
    _update(conn, job_id, status="running", stage="scraping",
            message=f"crawling {onion_url}")
    log.info("job %d: scraping %s", job_id, onion_url)

    from backend.scraper.html_client import HtmlForumClient

    with HtmlForumClient(onion_url, proxy=proxy) as client:
        # Per-source cursor: re-scraping this forum isn't held back by other
        # forums' newer posts. Only genuinely-new posts on THIS forum count.
        crawl = client.crawl(since=store.get_cursor(source=source), source=source)
    if crawl.errors:
        log.warning("job %d: crawl had %d error(s)", job_id, len(crawl.errors))
    inserted, duplicates = store.insert_posts(crawl.posts, source=source)
    _update(conn, job_id, posts_scraped=inserted,
            message=f"scraped {inserted} new posts "
                    f"({duplicates} already seen)")
    log.info("job %d: scraped inserted=%d duplicates=%d", job_id, inserted, duplicates)

    # Even if nothing new was scraped, a previous job for this forum may have
    # left posts un-enriched (interrupted run, Ollama was down, etc.). Drain
    # any pending pipeline work rather than bailing — the stages are idempotent
    # and no-op when there's genuinely nothing to do.
    pending = conn.execute(
        "SELECT COUNT(*) FROM raw_posts WHERE source = ? AND ("
        "  processed_at IS NULL"
        "  OR NOT EXISTS (SELECT 1 FROM post_processing_state pps "
        "     WHERE pps.raw_post_id = raw_posts.id AND pps.stage = 'mitre'))",
        (source,),
    ).fetchone()[0]
    if inserted == 0 and pending == 0:
        _update(conn, job_id, status="done", stage="done",
                message="no new posts — this forum is already fully enriched",
                finished_at=time.time())
        return
    if inserted == 0:
        log.info("job %d: 0 new posts, but %d pending — draining pipeline",
                 job_id, pending)

    # --- Stage 2: extract (spaCy NER + regex IOCs) -------------------- #
    _update(conn, job_id, stage="extracting", message="extracting IOCs + entities")
    log.info("job %d: extracting", job_id)

    from backend.pipeline.extract import EntityExtractor, IOCExtractor
    from backend.pipeline.run import run_once as extract_run_once

    ent = EntityExtractor()
    ioc = IOCExtractor()
    extract_run_once(store, ioc, ent, batch=200)
    extracted = conn.execute(
        "SELECT COUNT(*) FROM raw_posts WHERE source = ? AND processed_at IS NOT NULL",
        (source,),
    ).fetchone()[0]
    _update(conn, job_id, posts_extracted=extracted,
            message=f"extracted IOCs from {extracted} posts")

    # --- Stage 3: LLM enrichment (skippable; graceful if Ollama down) - #
    if skip_llm:
        log.info("job %d: LLM stage skipped (fast mode)", job_id)
        _update(conn, job_id, stage="llm", llm_skipped=1,
                message="fast mode — LLM enrichment skipped")
        llm_ok = False
    else:
        llm_ok = _try_llm(conn, store, job_id, ollama_url)

    # --- Stage 4: MITRE mapping + mitigations ------------------------ #
    _update(conn, job_id, stage="mitre", message="mapping to MITRE ATT&CK")
    log.info("job %d: MITRE matching", job_id)

    from backend.mitre import embed as embed_mod
    from backend.mitre.run import run_once as mitre_run_once

    # In fast mode the LLM stage was skipped, so posts have no 'llm' state row.
    # include_llmless lets MITRE still match them — pure semantic, no LLM
    # verification — so a fast-mode forum still gets ATT&CK technique mappings.
    mitre_run_once(
        store, model_name=embed_mod.DEFAULT_MODEL,
        batch=25, topk=5, threshold=0.45, limit=None,
        include_llmless=skip_llm,
    )
    mapped = conn.execute(
        "SELECT COUNT(*) FROM post_techniques pt "
        "JOIN raw_posts rp ON rp.id = pt.raw_post_id WHERE rp.source = ?",
        (source,),
    ).fetchone()[0]
    _update(conn, job_id, techniques_mapped=mapped)

    # --- done -------------------------------------------------------- #
    total_posts = conn.execute(
        "SELECT COUNT(*) FROM raw_posts WHERE source = ?", (source,)
    ).fetchone()[0]
    head = (f"done — {inserted} new posts scraped" if inserted
            else "done — resumed; no new posts")
    if llm_ok:
        llm_note = ""
    elif skip_llm:
        llm_note = "; LLM enrichment skipped (fast mode)"
    else:
        llm_note = "; LLM enrichment skipped (Ollama unreachable)"
    note = (f"{head}; {total_posts} total from this forum, "
            f"{mapped} technique mappings" + llm_note)
    _update(conn, job_id, status="done", stage="done",
            message=note, finished_at=time.time())
    log.info("job %d: %s", job_id, note)


def _try_llm(
    conn: sqlite3.Connection, store: Store, job_id: int, ollama_url: str
) -> bool:
    """Run the LLM stage if Ollama is reachable. Returns True on success.

    If Ollama is down, marks the job's llm_skipped flag and returns False —
    the job continues without LLM enrichment rather than failing.
    """
    from backend.llm.client import AsyncOllamaClient, OllamaError

    _update(conn, job_id, stage="checking-ollama",
            message="checking Ollama availability")

    # How many posts still need LLM analysis — the denominator for progress.
    def _pending() -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM raw_posts rp WHERE rp.processed_at IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM post_processing_state pps "
            "  WHERE pps.raw_post_id = rp.id AND pps.stage = 'llm')"
        ).fetchone()[0]

    def _analysed() -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM post_processing_state WHERE stage = 'llm'"
        ).fetchone()[0]

    async def _check_and_run() -> bool:
        client = AsyncOllamaClient(base_url=ollama_url)
        try:
            await client.health()
        except OllamaError as e:
            log.warning("job %d: Ollama unreachable, skipping LLM: %s", job_id, e)
            await client.aclose()
            return False
        try:
            from backend.llm.run import process_batch_async

            target = _pending()
            log.info("job %d: LLM enrichment of %d posts", job_id, target)
            # Run in small batches and update the job row between each, so the
            # frontend sees live progress and a death mid-stage doesn't lose
            # all completed work (each batch commits via post_processing_state).
            done_start = _analysed()
            while True:
                seen, _, _ = await process_batch_async(
                    store, client, batch_size=10, concurrency=4,
                )
                if seen == 0:
                    break
                done = _analysed() - done_start
                _update(conn, job_id, stage="llm", posts_llm=done,
                        message=f"LLM enrichment: {done}/{target} posts")
            return True
        finally:
            await client.aclose()

    try:
        ok = asyncio.run(_check_and_run())
    except Exception as e:  # noqa: BLE001 — LLM failure must not kill the job
        log.warning("job %d: LLM stage errored, continuing: %s", job_id, e)
        ok = False

    if not ok:
        _update(conn, job_id, llm_skipped=1,
                message="Ollama unreachable — continuing without LLM enrichment")
    return ok
