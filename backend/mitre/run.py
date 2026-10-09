"""MITRE stage: ingest ATT&CK data and map posts to techniques.
Run: python -m backend.mitre.run --ingest | --once | --watch | --reset | --reset-corpus
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time

import numpy as np

from backend.db.store import Store
from backend.mitre import embed as embed_mod
from backend.mitre import ingest as ingest_mod
from backend.mitre import match as match_mod

# Name of this stage in post_processing_state
STAGE = "mitre"
log = logging.getLogger("sentinelx.mitre")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------- ingest ---------------------------------------------------------- #

def ingest(store: Store, model_name: str, force_download: bool = False) -> int:
    """Download, parse, embed and save all MITRE techniques; returns count."""
    log.info("ingest: downloading + parsing MITRE Enterprise ATT&CK corpus")
    # 1. Download + parse the MITRE file
    techniques = ingest_mod.fetch_and_parse(force_download=force_download)
    log.info("ingest: parsed %d techniques (incl. sub-techniques)", len(techniques))

    log.info("ingest: embedding (model=%s) -- this loads sentence-transformers, may take a few seconds...", model_name)
    # 2. Turn each technique's name + description into a vector
    texts = [f"{t.name}. {t.description}" for t in techniques]
    t0 = time.time()
    vecs = embed_mod.encode(texts, model_name=model_name)
    log.info("ingest: embedded %d techniques in %.1fs", len(vecs), time.time() - t0)

    # 3. Save every technique (insert new, update existing)
    now = time.time()
    conn = store.conn
    upserted = 0
    for t, v in zip(techniques, vecs):
        conn.execute(
            """
            INSERT INTO mitre_techniques
                (technique_id, name, description, tactics, url,
                 is_subtechnique, parent_id, embedding, embedding_model, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(technique_id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                tactics = excluded.tactics,
                url = excluded.url,
                is_subtechnique = excluded.is_subtechnique,
                parent_id = excluded.parent_id,
                embedding = excluded.embedding,
                embedding_model = excluded.embedding_model,
                ingested_at = excluded.ingested_at
            """,
            (
                t.technique_id,
                t.name,
                t.description,
                ",".join(t.tactics),
                t.url,
                1 if t.is_subtechnique else 0,
                t.parent_id,
                embed_mod.to_blob(v),
                model_name,
                now,
            ),
        )
        upserted += 1
    conn.commit()
    log.info("ingest: upserted %d rows into mitre_techniques", upserted)
    return upserted


# ---------- mitigations ingest ---------------------------------------------- #

def ingest_mitigations(store: Store) -> tuple[int, int]:
    """Load MITRE mitigations + technique links from cached data; returns (mitigations, links)."""
    log.info("ingest-mitigations: parsing course-of-action objects from cached corpus")
    # Read mitigations and their technique links from the cached MITRE file
    mitigations, links = ingest_mod.parse_mitigations()
    log.info("ingest-mitigations: parsed %d mitigations, %d technique links",
             len(mitigations), len(links))

    now = time.time()
    conn = store.conn

    # Save each mitigation (insert new, update existing)
    for m in mitigations:
        conn.execute(
            """
            INSERT INTO mitre_mitigations (mitigation_id, name, description, url, ingested_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(mitigation_id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                url = excluded.url,
                ingested_at = excluded.ingested_at
            """,
            (m.mitigation_id, m.name, m.description, m.url, now),
        )

    # Skip links to techniques we don't have
    known = {r[0] for r in conn.execute("SELECT technique_id FROM mitre_techniques")}
    linked = 0
    skipped = 0
    # Save each technique <-> mitigation link
    for ln in links:
        if known and ln.technique_id not in known:
            skipped += 1
            continue
        conn.execute(
            """
            INSERT INTO technique_mitigations (technique_id, mitigation_id, ingested_at)
            VALUES (?, ?, ?)
            ON CONFLICT(technique_id, mitigation_id) DO UPDATE SET
                ingested_at = excluded.ingested_at
            """,
            (ln.technique_id, ln.mitigation_id, now),
        )
        linked += 1
    conn.commit()
    if skipped:
        log.warning("ingest-mitigations: skipped %d links to techniques not in corpus "
                    "(run --ingest first for full coverage)", skipped)
    log.info("ingest-mitigations: upserted %d mitigations, %d links", len(mitigations), linked)
    return len(mitigations), linked


# ---------- match cursor ---------------------------------------------------- #

def _fetch_unmatched(
    conn: sqlite3.Connection, limit: int, include_llmless: bool = False
) -> list[sqlite3.Row]:
    """Posts ready for MITRE matching (include_llmless=True also takes posts with no LLM run)."""
    # Normal: only posts the LLM already analysed. Fast mode: any extracted post
    gate = (
        "rp.processed_at IS NOT NULL"
        if include_llmless
        else "EXISTS (SELECT 1 FROM post_processing_state pps_llm "
             "WHERE pps_llm.raw_post_id = rp.id AND pps_llm.stage = 'llm')"
    )
    # Use the English translation for matching
    return conn.execute(
        f"""
        SELECT rp.id, COALESCE(rp.body_en, rp.body) AS body, la.techniques_json
        FROM raw_posts rp
        LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id
        WHERE {gate}
          AND NOT EXISTS (
            SELECT 1 FROM post_processing_state pps
            WHERE pps.raw_post_id = rp.id AND pps.stage = ?
          )
        ORDER BY rp.id
        LIMIT ?
        """,
        (STAGE, limit),
    ).fetchall()


def _persist_matches(
    conn: sqlite3.Connection, raw_post_id: int, matches: list[match_mod.Match]
) -> tuple[int, int, int]:
    now = time.time()
    v = u = s = 0
    # Save each match and count how it was found (v = verified, u = unverified, s = semantic)
    for m in matches:
        try:
            conn.execute(
                """
                INSERT INTO post_techniques
                    (raw_post_id, technique_id, source, score, evidence, matched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (raw_post_id, m.technique_id, m.source, m.score, m.evidence, now),
            )
            if m.source == "llm_verified":
                v += 1
            elif m.source == "llm_unverified":
                u += 1
            else:
                s += 1
        except sqlite3.IntegrityError:
            # UNIQUE(raw_post_id, technique_id, source) - already had this match.
            pass
    # Mark this post as done for the MITRE stage
    conn.execute(
        "INSERT OR REPLACE INTO post_processing_state (raw_post_id, stage, processed_at) "
        "VALUES (?, ?, ?)",
        (raw_post_id, STAGE, now),
    )
    return v, u, s


def process_batch(
    store: Store,
    corpus_ids: list[str],
    corpus_matrix: np.ndarray,
    corpus_id_set: set[str],
    model_name: str,
    batch_size: int,
    topk: int,
    threshold: float,
    include_llmless: bool = False,
) -> tuple[int, int, int, int]:
    """Process up to batch_size posts. Returns (seen, verified, unverified, semantic)."""
    conn = store.conn
    # Log this run in mitre_runs
    started = time.time()
    cur = conn.execute("INSERT INTO mitre_runs (started_at) VALUES (?)", (started,))
    run_id = cur.lastrowid
    conn.commit()

    seen = v_total = u_total = s_total = 0
    err: str | None = None
    try:
        # Get the next posts to match
        rows = _fetch_unmatched(conn, batch_size, include_llmless=include_llmless)
        seen = len(rows)
        if rows:
            # Chunk all posts and embed in one batch (faster); `bounds` maps chunks back to posts
            chunks: list[str] = []
            bounds: list[tuple[int, int]] = []
            for r in rows:
                parts = match_mod.chunk_text(r["body"] or "")
                bounds.append((len(chunks), len(chunks) + len(parts)))
                chunks.extend(parts)
            t0 = time.time()
            chunk_vecs = embed_mod.encode(chunks, model_name=model_name)
            log.debug("embedded %d chunks for %d posts in %.2fs",
                      len(chunks), len(rows), time.time() - t0)
            post_vecs = [chunk_vecs[a:b] for a, b in bounds]

            # For each post: check the LLM's T-codes, then find more by similarity
            for row, post_vec in zip(rows, post_vecs):
                candidates = match_mod.parse_llm_candidates(row["techniques_json"])
                llm_matches = match_mod.verify_llm(candidates, corpus_id_set)
                # Skip techniques the LLM already got right, so they aren't counted twice
                exclude = {m.technique_id for m in llm_matches if m.source == "llm_verified"}
                sem_matches = match_mod.semantic_topk(
                    post_vec, corpus_matrix, corpus_ids,
                    topk=topk, threshold=threshold, exclude=exclude,
                )
                v, u, s = _persist_matches(conn, row["id"], llm_matches + sem_matches)
                v_total += v; u_total += u; s_total += s
                log.info("post id=%d verified=%d unverified=%d semantic=%d",
                         row["id"], v, u, s)
            conn.commit()
    except Exception as e:
        err = repr(e)
        conn.commit()
        raise
    # Always finish the mitre_runs log row
    finally:
        conn.execute(
            "UPDATE mitre_runs SET finished_at = ?, posts_seen = ?, "
            "verified_inserted = ?, unverified_inserted = ?, semantic_inserted = ?, "
            "error = ? WHERE id = ?",
            (time.time(), seen, v_total, u_total, s_total, err, run_id),
        )
        conn.commit()

    return seen, v_total, u_total, s_total


def run_once(store: Store, model_name: str, batch: int, topk: int,
             threshold: float, limit: int | None,
             include_llmless: bool = False) -> None:
    # Load all technique vectors once
    ids, matrix, id_set = match_mod.load_corpus(store.conn)
    if not ids:
        log.error("mitre_techniques is empty -- run with --ingest first")
        return
    log.info("loaded corpus: %d techniques", len(ids))

    total = 0
    # Keep processing batches until no posts are left (or the limit is reached)
    while True:
        remaining = (limit - total) if limit is not None else batch
        if limit is not None and remaining <= 0:
            return
        size = min(batch, remaining) if limit is not None else batch
        seen, v, u, s = process_batch(
            store, ids, matrix, id_set, model_name, size, topk, threshold,
            include_llmless=include_llmless,
        )
        total += seen
        if seen < size:
            return


def run_watch(store: Store, model_name: str, batch: int, topk: int,
              threshold: float, interval: float) -> None:
    log.info("watch mode: every %.1fs (Ctrl-C to stop)", interval)
    # Repeat forever, waiting `interval` seconds between runs
    while True:
        try:
            run_once(store, model_name, batch, topk, threshold, limit=None)
        except Exception as e:
            log.warning("batch errored, will retry: %s", e)
        time.sleep(interval)


# Delete all post -> technique matches (MITRE data itself stays)
def reset_matches(store: Store) -> None:
    store.conn.executescript(
        "DELETE FROM post_techniques; DELETE FROM mitre_runs;"
        f"DELETE FROM post_processing_state WHERE stage = '{STAGE}';"
        "DELETE FROM sqlite_sequence WHERE name IN ('post_techniques','mitre_runs');"
    )
    store.conn.commit()


# Delete the MITRE data itself (needs --ingest again)
def reset_corpus(store: Store) -> None:
    store.conn.executescript(
        "DELETE FROM technique_mitigations; DELETE FROM mitre_mitigations;"
        "DELETE FROM mitre_techniques;"
    )
    store.conn.commit()


def main(argv: list[str] | None = None) -> int:
    # Command-line options
    p = argparse.ArgumentParser(prog="sentinelx-mitre")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--ingest", action="store_true",
                      help="download + parse + embed the MITRE corpus")
    mode.add_argument("--ingest-mitigations", action="store_true", dest="ingest_mitigations",
                      help="parse + upsert MITRE mitigations from the cached corpus "
                           "(offline, no embedding; run --ingest at least once first)")
    mode.add_argument("--once", action="store_true",
                      help="match all unmatched posts and exit (default)")
    mode.add_argument("--watch", action="store_true")
    mode.add_argument("--reset", action="store_true",
                      help="wipe post_techniques + mitre_runs + mitre rows in post_processing_state")
    mode.add_argument("--reset-corpus", action="store_true",
                      help="wipe mitre_techniques (forces re-ingest)")

    p.add_argument("--force-download", action="store_true",
                   help="re-download corpus even if cached (use with --ingest)")
    p.add_argument("--interval", type=float, default=60.0)
    p.add_argument("--batch", type=int, default=25,
                   help="posts per mitre_runs row (also commit boundary)")
    p.add_argument("--limit", type=int, default=None,
                   help="stop after this many posts (smoke test). --once only.")
    p.add_argument("--topk", type=int, default=5,
                   help="max semantic-discovery techniques per post")
    p.add_argument("--threshold", type=float, default=0.45,
                   help="min cosine similarity for semantic discovery")
    p.add_argument("--model", default=embed_mod.DEFAULT_MODEL)
    p.add_argument("--db", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    _setup_logging(args.verbose)

    store = Store(args.db) if args.db else Store()
    # Run whichever mode was picked
    try:
        if args.reset:
            reset_matches(store)
            log.info("mitre match state reset")
            return 0
        if args.reset_corpus:
            reset_corpus(store)
            log.info("mitre corpus wiped (re-ingest with --ingest)")
            return 0
        if args.ingest:
            ingest(store, model_name=args.model, force_download=args.force_download)
            ingest_mitigations(store)
            return 0
        if args.ingest_mitigations:
            ingest_mitigations(store)
            return 0
        if args.watch:
            run_watch(store, args.model, args.batch, args.topk, args.threshold, args.interval)
        else:
            run_once(store, args.model, args.batch, args.topk, args.threshold, args.limit)
        return 0
    except KeyboardInterrupt:
        log.info("interrupted")
        return 130
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
