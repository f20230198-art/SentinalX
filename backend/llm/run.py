"""LLM stage: run the 4-prompt chain on extracted posts and save results.
Run: python -m backend.llm.run --once | --watch | --reset
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
import time

from backend.db.store import Store
from backend.llm.chain import Analysis, analyse_post_async
from backend.llm.client import AsyncOllamaClient, OllamaError

# Name of this stage in post_processing_state
STAGE = "llm"

log = logging.getLogger("sentinelx.llm")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# Posts that finished extraction but haven't been through the LLM yet
def _fetch_unanalysed(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    # Use the English translation if there is one
    return conn.execute(
        """
        SELECT rp.id, rp.thread_title, rp.category,
               COALESCE(rp.body_en, rp.body) AS body
        FROM raw_posts rp
        WHERE rp.processed_at IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM post_processing_state pps
              WHERE pps.raw_post_id = rp.id AND pps.stage = ?
          )
        ORDER BY rp.id
        LIMIT ?
        """,
        (STAGE, limit),
    ).fetchall()


# IOCs and entities already found for this post (given to the LLM as known facts)
def _fetch_facts(conn: sqlite3.Connection, raw_post_id: int) -> tuple[list[dict], list[dict]]:
    iocs = [dict(r) for r in conn.execute(
        "SELECT ioc_type, value FROM iocs WHERE raw_post_id = ?", (raw_post_id,)
    ).fetchall()]
    ents = [dict(r) for r in conn.execute(
        "SELECT label, text FROM entities WHERE raw_post_id = ?", (raw_post_id,)
    ).fetchall()]
    return iocs, ents


# Save (or overwrite) the LLM results for one post
def _persist(conn: sqlite3.Connection, raw_post_id: int, model: str, a: Analysis) -> None:
    now = time.time()
    conn.execute(
        """
        INSERT INTO llm_analyses
            (raw_post_id, summary, intent, targets_json, techniques_json,
             model, analysed_at, raw_responses)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(raw_post_id) DO UPDATE SET
            summary = excluded.summary,
            intent = excluded.intent,
            targets_json = excluded.targets_json,
            techniques_json = excluded.techniques_json,
            model = excluded.model,
            analysed_at = excluded.analysed_at,
            raw_responses = excluded.raw_responses
        """,
        (
            raw_post_id,
            a.summary,
            (a.intent or {}).get("intent") if a.intent else None,
            json.dumps(a.targets) if a.targets is not None else None,
            json.dumps(a.techniques) if a.techniques is not None else None,
            model,
            now,
            json.dumps({k: {"response": v.get("response", "")} for k, v in a.raw_responses.items()}),
        ),
    )
    # Mark this post as done for the LLM stage
    conn.execute(
        "INSERT OR REPLACE INTO post_processing_state (raw_post_id, stage, processed_at) "
        "VALUES (?, ?, ?)",
        (raw_post_id, STAGE, now),
    )


async def _analyse_one(
    sem: asyncio.Semaphore,
    client: AsyncOllamaClient,
    row: sqlite3.Row,
    iocs: list[dict],
    ents: list[dict],
) -> tuple[sqlite3.Row, Analysis, float]:
    # The semaphore limits how many posts run at the same time
    async with sem:
        t0 = time.time()
        a = await analyse_post_async(
            client, row["thread_title"], row["category"], row["body"], iocs, ents,
        )
        return row, a, time.time() - t0


async def process_batch_async(
    store: Store,
    client: AsyncOllamaClient,
    batch_size: int,
    concurrency: int,
) -> tuple[int, int, int]:
    """Analyse up to batch_size posts (`concurrency` at once); returns (seen, ok, failed)."""
    # Log this run in llm_runs
    conn = store.conn
    started = time.time()
    cur = conn.execute(
        "INSERT INTO llm_runs (started_at, model) VALUES (?, ?)",
        (started, client.model),
    )
    run_id = cur.lastrowid
    conn.commit()

    seen = ok = failed = 0
    err: str | None = None

    # Get the next posts to analyse
    try:
        rows = _fetch_unanalysed(conn, batch_size)
        seen = len(rows)
        if rows:
            sem = asyncio.Semaphore(concurrency)
            # Load DB data first so async tasks never touch SQLite
            jobs = []
            for row in rows:
                iocs, ents = _fetch_facts(conn, row["id"])
                log.info("queued post id=%d (%s) iocs=%d entities=%d",
                         row["id"], row["category"], len(iocs), len(ents))
                jobs.append(_analyse_one(sem, client, row, iocs, ents))

            # Save each post as soon as its analysis finishes
            for fut in asyncio.as_completed(jobs):
                row, a, elapsed = await fut
                if a.ok:
                    ok += 1
                    log.info("post id=%d ok in %.1fs intent=%s", row["id"],
                             elapsed, (a.intent or {}).get("intent"))
                else:
                    failed += 1
                    log.warning("post id=%d failed in %.1fs errors=%s",
                                row["id"], elapsed, a.errors)
                _persist(conn, row["id"], client.model, a)
                conn.commit()
    # On error, keep what was saved and re-raise
    except Exception as e:
        err = repr(e)
        conn.commit()
        raise
    finally:
        # Always finish the llm_runs log row
        conn.execute(
            "UPDATE llm_runs SET finished_at = ?, posts_seen = ?, "
            "posts_completed = ?, posts_failed = ?, error = ? WHERE id = ?",
            (time.time(), seen, ok, failed, err, run_id),
        )
        conn.commit()

    return seen, ok, failed


async def run_once_async(
    store: Store, client: AsyncOllamaClient,
    batch: int, concurrency: int, limit: int | None,
) -> None:
    # Keep processing batches until no posts are left (or the limit is reached)
    total = 0
    while True:
        remaining = (limit - total) if limit is not None else batch
        if limit is not None and remaining <= 0:
            return
        size = min(batch, remaining) if limit is not None else batch
        seen, _, _ = await process_batch_async(store, client, size, concurrency)
        total += seen
        if seen < size:
            return


async def run_watch_async(
    store: Store, client: AsyncOllamaClient,
    batch: int, concurrency: int, interval: float,
) -> None:
    log.info("watch mode: every %.1fs concurrency=%d (Ctrl-C to stop)",
             interval, concurrency)
    # Repeat forever, waiting `interval` seconds between runs
    while True:
        try:
            await run_once_async(store, client, batch, concurrency, limit=None)
        except Exception as e:
            log.warning("batch errored, will retry: %s", e)
        await asyncio.sleep(interval)


# Delete all LLM results so every post gets re-analysed
def reset_llm(store: Store) -> None:
    store.conn.executescript(
        "DELETE FROM llm_analyses; DELETE FROM llm_runs;"
        f"DELETE FROM post_processing_state WHERE stage = '{STAGE}';"
        "DELETE FROM sqlite_sequence WHERE name IN ('llm_analyses','llm_runs');"
    )
    store.conn.commit()


async def _amain(args, store: Store) -> int:
    # Check Ollama is running and has the model before starting
    client = AsyncOllamaClient(model=args.model)
    try:
        installed = await client.health()
    except OllamaError as e:
        log.error("ollama health check failed: %s", e)
        await client.aclose()
        return 1
    if not any(args.model in name for name in installed):
        log.error("model %r not in ollama (installed: %s)", args.model, installed)
        await client.aclose()
        return 1
    log.info("ollama ok, using model=%s concurrency=%d", args.model, args.concurrency)

    # Run once or keep watching for new posts
    try:
        if args.watch:
            await run_watch_async(
                store, client, args.batch, args.concurrency, args.interval,
            )
        else:
            await run_once_async(
                store, client, args.batch, args.concurrency, args.limit,
            )
    finally:
        await client.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    # Command-line options
    p = argparse.ArgumentParser(prog="sentinelx-llm")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true",
                      help="process all unanalysed posts and exit (default)")
    mode.add_argument("--watch", action="store_true")
    mode.add_argument("--reset", action="store_true",
                      help="wipe llm_analyses + llm_runs + llm rows in post_processing_state")

    p.add_argument("--interval", type=float, default=60.0)
    p.add_argument("--batch", type=int, default=10,
                   help="posts per llm_runs row (also commit boundary)")
    p.add_argument("--concurrency", type=int, default=2,
                   help="posts processed in parallel (each post fans out 4 prompts).")
    p.add_argument("--limit", type=int, default=None,
                   help="stop after this many posts (smoke test). --once only.")
    p.add_argument("--model", default="mistral")
    p.add_argument("--db", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    _setup_logging(args.verbose)

    store = Store(args.db) if args.db else Store()

    # --reset: wipe LLM results and exit
    if args.reset:
        reset_llm(store)
        log.info("llm state reset")
        store.close()
        return 0

    try:
        return asyncio.run(_amain(args, store))
    except KeyboardInterrupt:
        log.info("interrupted")
        return 130
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
