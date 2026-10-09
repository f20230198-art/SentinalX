"""Runs the full pipeline for one .onion URL in a background thread (skips LLM if Ollama is down)."""

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


# Read one job row (None if it doesn't exist)
def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM pipeline_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    return dict(row) if row else None


# Most recent jobs first
def list_jobs(conn: sqlite3.Connection, limit: int = 25) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM pipeline_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def _update(conn: sqlite3.Connection, job_id: int, **fields) -> None:
    """Patch named columns on a job row; always bumps updated_at."""
    # Build "col1 = ?, col2 = ?" from the given fields and save them
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
    urls: list[str] | None = None,
) -> None:
    """Scrape -> extract -> LLM -> MITRE for one job; writes progress/errors to the job row, never raises."""
    # The job opens its own DB connection (never shares the API's)
    store = Store(db_path) if db_path else Store()
    conn = store.conn
    try:
        # Run every stage; any crash is saved on the job row as an error
        _run_stages(conn, store, job_id, onion_url, source, proxy, ollama_url,
                    skip_llm, urls)
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
    urls: list[str] | None = None,
) -> None:
    # --- Step 1: scrape (HTML) ---------------------------------------- #
    # Pages mode: read specific pages picked on the Discover page
    if urls:
        inserted, duplicates, page_sources = _read_pages(conn, store, job_id, urls, proxy)
    # Forum mode: crawl the whole forum
    else:
        _update(conn, job_id, status="running", stage="scraping",
                message=f"crawling {onion_url}")
        log.info("job %d: scraping %s", job_id, onion_url)

        from backend.scraper.html_client import HtmlForumClient

        with HtmlForumClient(onion_url, proxy=proxy) as client:
            # Only count posts newer than this forum's last scrape
            crawl = client.crawl(since=store.get_cursor(source=source), source=source)
        if crawl.errors:
            log.warning("job %d: crawl had %d error(s)", job_id, len(crawl.errors))
        inserted, duplicates = store.insert_posts(crawl.posts, source=source)
        page_sources = [source]
    _update(conn, job_id, posts_scraped=inserted,
            message=f"scraped {inserted} new posts "
                    f"({duplicates} already seen)")
    log.info("job %d: scraped inserted=%d duplicates=%d", job_id, inserted, duplicates)

    # Even with nothing new, finish leftover posts from earlier runs
    # Count posts from this forum that still need extraction or MITRE matching
    src_in = ",".join("?" for _ in page_sources)
    pending = conn.execute(
        f"SELECT COUNT(*) FROM raw_posts WHERE source IN ({src_in}) AND ("
        "  processed_at IS NULL"
        "  OR NOT EXISTS (SELECT 1 FROM post_processing_state pps "
        "     WHERE pps.raw_post_id = raw_posts.id AND pps.stage = 'mitre'))",
        page_sources,
    ).fetchone()[0]
    # Nothing new and nothing pending -> job is done
    if inserted == 0 and pending == 0:
        _update(conn, job_id, status="done", stage="done",
                message="no new posts — this forum is already fully enriched",
                finished_at=time.time())
        return
    if inserted == 0:
        log.info("job %d: 0 new posts, but %d pending — draining pipeline",
                 job_id, pending)

    # --- Step 2: extract (spaCy NER + regex IOCs) --------------------- #
    _update(conn, job_id, stage="extracting", message="extracting IOCs + entities")
    log.info("job %d: extracting", job_id)

    from backend.pipeline.extract import EntityExtractor, IOCExtractor
    from backend.pipeline.run import run_once as extract_run_once

    # Reuse the normal extraction stage code
    ent = EntityExtractor()
    ioc = IOCExtractor()
    extract_run_once(store, ioc, ent, batch=200)
    # How many of this forum's posts are now extracted
    extracted = conn.execute(
        f"SELECT COUNT(*) FROM raw_posts WHERE source IN ({src_in}) AND processed_at IS NOT NULL",
        page_sources,
    ).fetchone()[0]
    _update(conn, job_id, posts_extracted=extracted,
            message=f"extracted IOCs from {extracted} posts")

    # --- Step 3: LLM enrichment (skippable; graceful if Ollama down) -- #
    # Fast mode skips the LLM; otherwise try it (skipped automatically if Ollama is down)
    if skip_llm:
        log.info("job %d: LLM stage skipped (fast mode)", job_id)
        _update(conn, job_id, stage="llm", llm_skipped=1,
                message="fast mode — LLM enrichment skipped")
        llm_ok = False
    else:
        llm_ok = _try_llm(conn, store, job_id, ollama_url)

    # --- Step 4: MITRE mapping + mitigations -------------------------- #
    _update(conn, job_id, stage="mitre", message="mapping to MITRE ATT&CK")
    log.info("job %d: MITRE matching", job_id)

    from backend.mitre import embed as embed_mod
    from backend.mitre.run import run_once as mitre_run_once

    # Fast mode (no LLM): still map techniques with semantic matching
    mitre_run_once(
        store, model_name=embed_mod.DEFAULT_MODEL,
        batch=25, topk=5, threshold=0.45, limit=None,
        include_llmless=skip_llm,
    )
    # How many technique matches this forum's posts now have
    mapped = conn.execute(
        "SELECT COUNT(*) FROM post_techniques pt "
        f"JOIN raw_posts rp ON rp.id = pt.raw_post_id WHERE rp.source IN ({src_in})",
        page_sources,
    ).fetchone()[0]
    _update(conn, job_id, techniques_mapped=mapped)

    # --- done -------------------------------------------------------- #
    # Build the final summary message for the UI
    total_posts = conn.execute(
        f"SELECT COUNT(*) FROM raw_posts WHERE source IN ({src_in})", page_sources
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


def _read_pages(
    conn: sqlite3.Connection, store: Store, job_id: int, urls: list[str], proxy: str,
) -> tuple[int, int, list[str]]:
    """Pages mode: fetch each URL over Tor and read it as a post (failures counted, not fatal)."""
    import httpx

    from backend.discovery.page_reader import read_page

    # Fetch each page over Tor and turn it into a post
    _update(conn, job_id, status="running", stage="scraping",
            message=f"reading {len(urls)} discovered pages over Tor")
    posts, failed = [], 0
    timeout = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=60.0)
    with httpx.Client(proxy=proxy, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"}) as client:
        for i, url in enumerate(urls, 1):
            try:
                r = client.get(url if "://" in url else f"http://{url}")
                r.raise_for_status()
                post = read_page(r.text, url)
                if post:
                    posts.append(post)
                else:
                    failed += 1
            except httpx.HTTPError as e:
                log.warning("job %d: page %s failed: %s", job_id, url, e)
                failed += 1
            _update(conn, job_id, message=f"read {i}/{len(urls)} pages ({failed} unreadable)")
    # Save each post under its own .onion host as the source
    inserted = duplicates = 0
    for p in posts:  # one source per page host
        ins, dup = store.insert_posts([p], source=p["source"])
        inserted += ins
        duplicates += dup
    _update(conn, job_id, posts_scraped=inserted,
            message=f"read {len(posts)} pages: {inserted} new, {duplicates} already seen, {failed} unreadable")
    return inserted, duplicates, sorted({p["source"] for p in posts}) or ["__none__"]


def _try_llm(
    conn: sqlite3.Connection, store: Store, job_id: int, ollama_url: str
) -> bool:
    """Run the LLM stage if Ollama is up; otherwise mark llm_skipped and return False."""
    from backend.llm.client import AsyncOllamaClient, OllamaError

    # Check Ollama first; if it's down, skip the LLM step
    _update(conn, job_id, stage="checking-ollama",
            message="checking Ollama availability")

    # How many posts still need LLM analysis — the denominator for progress.
    def _pending() -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM raw_posts rp WHERE rp.processed_at IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM post_processing_state pps "
            "  WHERE pps.raw_post_id = rp.id AND pps.stage = 'llm')"
        ).fetchone()[0]

    # How many posts have finished the LLM stage overall
    def _analysed() -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM post_processing_state WHERE stage = 'llm'"
        ).fetchone()[0]

    async def _check_and_run() -> bool:
        # Make sure Ollama answers before starting
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
            # Small batches so the UI sees live progress and work isn't lost on a crash
            done_start = _analysed()
            # Keep analysing small batches until none are left, updating progress each time
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

    # Run the async code; any error just skips the LLM step
    try:
        ok = asyncio.run(_check_and_run())
    except Exception as e:  # noqa: BLE001 — LLM failure must not kill the job
        log.warning("job %d: LLM stage errored, continuing: %s", job_id, e)
        ok = False

    if not ok:
        _update(conn, job_id, llm_skipped=1,
                message="Ollama unreachable — continuing without LLM enrichment")
    return ok
