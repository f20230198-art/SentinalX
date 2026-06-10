"""Extraction pipeline entrypoint.

Reads unprocessed rows from raw_posts (processed_at IS NULL), detects each
post's language, translates non-English posts to English, runs IOC + NER
extraction, persists results to iocs / entities, and stamps processed_at.

Multilingual ingestion:
    Before extraction, each post's language is detected (langdetect). When the
    body is not English, it is translated to English (argostranslate, offline)
    and stored in raw_posts.body_en. IOC regexes run on the ORIGINAL body
    (IOCs are language-agnostic and translation can mangle long hashes); NER
    runs on the English text so spaCy's en_core_web_sm stays effective. The
    LLM and MITRE matchers downstream read body_en too.

Usage:
    python -m backend.pipeline.run --once
    python -m backend.pipeline.run --watch --interval 30
    python -m backend.pipeline.run --reset      # re-extract everything
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time

from backend.db.store import Store
from backend.lang import detect_language, translate_to_english
from backend.pipeline.extract import (
    EntityExtractor,
    IOCExtractor,
    dedupe,
)

log = logging.getLogger("sentinelx.pipeline")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _fetch_unprocessed(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, body, lang, body_en FROM raw_posts WHERE processed_at IS NULL "
        "ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()


def _resolve_language(row: sqlite3.Row) -> tuple[str, float | None, str | None]:
    """Detect language and translate the post body if it is not English.

    Returns (lang, lang_confidence, body_en):
      * lang            ISO code, or 'unknown'.
      * lang_confidence langdetect probability, or None for 'unknown'.
      * body_en         English translation, or None when the post is already
                        English / undetectable (no translation stored).

    If a previous run already populated `row["lang"]`, that result is trusted
    and we don't re-detect — keeps re-extraction (--reset on iocs/entities only)
    cheap. A full language re-run is `pipeline.run --reset`, which clears these.
    """
    # Already resolved on an earlier pass — reuse it.
    if row["lang"]:
        return row["lang"], None, row["body_en"]

    det = detect_language(row["body"])
    if not det.needs_translation:
        # No translation needed. This covers three cases:
        #   - genuinely English  -> store 'en'
        #   - undetectable       -> store 'unknown'
        #   - a low-confidence non-English guess on a jargon/IOC-heavy post,
        #     which we deliberately treat as English (see DetectionResult.
        #     needs_translation) -> normalise to 'en' so the UI doesn't badge
        #     it with a language we didn't actually trust.
        effective = det.lang if det.lang in ("en", "unknown") else "en"
        return effective, det.confidence, None

    tr = translate_to_english(row["body"], det.lang)
    if not tr.ok:
        # Translation degraded (no package / offline). Keep the detected
        # language so the UI can still flag the post, but store no body_en;
        # downstream stages fall back to the original body.
        log.warning("post translation degraded lang=%s — using original body",
                    det.lang)
        return det.lang, det.confidence, None
    return det.lang, det.confidence, tr.text


def _insert_iocs(conn: sqlite3.Connection, raw_post_id: int, matches, now: float) -> int:
    n = 0
    for m in matches:
        try:
            conn.execute(
                "INSERT INTO iocs (raw_post_id, ioc_type, value, span_start, span_end, extracted_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (raw_post_id, m.type, m.value, m.span[0], m.span[1], now),
            )
            n += 1
        except sqlite3.IntegrityError:
            pass
    return n


def _insert_entities(conn: sqlite3.Connection, raw_post_id: int, matches, now: float) -> int:
    n = 0
    for m in matches:
        try:
            conn.execute(
                "INSERT INTO entities (raw_post_id, label, text, span_start, span_end, extracted_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (raw_post_id, m.type, m.value, m.span[0], m.span[1], now),
            )
            n += 1
        except sqlite3.IntegrityError:
            pass
    return n


def process_batch(
    store: Store,
    ioc: IOCExtractor,
    ent: EntityExtractor,
    batch_size: int,
) -> tuple[int, int, int]:
    """Process up to batch_size unprocessed posts.

    For each post: detect language, translate non-English bodies to English,
    then extract IOCs (from the original body) and entities (from the English
    text). Persists language fields + body_en alongside the extraction state.

    Returns (posts_seen, iocs_inserted, entities_inserted).
    """
    conn = store.conn
    started = time.time()
    cur = conn.execute(
        "INSERT INTO extraction_runs (started_at) VALUES (?)", (started,)
    )
    run_id = cur.lastrowid
    conn.commit()

    posts_seen = 0
    iocs_inserted = 0
    entities_inserted = 0
    err: str | None = None

    try:
        rows = _fetch_unprocessed(conn, batch_size)
        for row in rows:
            now = time.time()

            # Language detection + translation. body_en is the English text
            # NER + LLM should see; None means "post is English, use body".
            lang, lang_conf, body_en = _resolve_language(row)
            english_text = body_en or row["body"]

            # IOCs from the ORIGINAL body — they're language-agnostic and
            # translation can corrupt long hashes / BTC addresses. NER from the
            # English text — spaCy's en_core_web_sm is English-only.
            iocs = dedupe(ioc.extract(row["body"]))
            ents = dedupe(ent.extract(english_text))
            iocs_inserted += _insert_iocs(conn, row["id"], iocs, now)
            entities_inserted += _insert_entities(conn, row["id"], ents, now)
            conn.execute(
                "UPDATE raw_posts SET processed_at = ?, lang = ?, "
                "lang_confidence = ?, body_en = ? WHERE id = ?",
                (now, lang, lang_conf, body_en, row["id"]),
            )
            posts_seen += 1
        conn.commit()
    except Exception as e:
        err = repr(e)
        conn.commit()
        raise
    finally:
        conn.execute(
            "UPDATE extraction_runs SET finished_at = ?, posts_seen = ?, "
            "iocs_inserted = ?, entities_inserted = ?, error = ? WHERE id = ?",
            (time.time(), posts_seen, iocs_inserted, entities_inserted, err, run_id),
        )
        conn.commit()

    log.info(
        "processed posts=%d iocs+=%d entities+=%d",
        posts_seen, iocs_inserted, entities_inserted,
    )
    return posts_seen, iocs_inserted, entities_inserted


def run_once(store: Store, ioc: IOCExtractor, ent: EntityExtractor, batch: int) -> None:
    while True:
        seen, _, _ = process_batch(store, ioc, ent, batch)
        if seen < batch:
            break


def run_watch(
    store: Store, ioc: IOCExtractor, ent: EntityExtractor, batch: int, interval: float
) -> None:
    log.info("watch mode: every %.1fs (Ctrl-C to stop)", interval)
    while True:
        try:
            run_once(store, ioc, ent, batch)
        except Exception as e:
            log.warning("batch errored, will retry: %s", e)
        time.sleep(interval)


def reset_extractions(store: Store) -> None:
    # Also clears the language fields so a re-run re-detects and re-translates
    # from scratch (re-extraction is the only way to refresh body_en — there is
    # no separate language-step cursor).
    store.conn.executescript(
        "DELETE FROM iocs; DELETE FROM entities; DELETE FROM extraction_runs;"
        "UPDATE raw_posts SET processed_at = NULL, lang = NULL, "
        "  lang_confidence = NULL, body_en = NULL;"
        "DELETE FROM sqlite_sequence WHERE name IN ('iocs','entities','extraction_runs');"
    )
    store.conn.commit()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sentinelx-pipeline")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true",
                      help="drain all unprocessed posts and exit (default)")
    mode.add_argument("--watch", action="store_true", help="loop forever")
    mode.add_argument("--reset", action="store_true",
                      help="wipe iocs/entities/extraction_runs and clear processed_at")

    p.add_argument("--interval", type=float, default=30.0)
    p.add_argument("--batch", type=int, default=200,
                   help="posts per batch (also extraction_runs row granularity)")
    p.add_argument("--db", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    _setup_logging(args.verbose)

    store = Store(args.db) if args.db else Store()

    if args.reset:
        reset_extractions(store)
        log.info("extraction state reset")
        store.close()
        return 0

    log.info("loading spaCy model en_core_web_sm "
             "(language detection + translation enabled)")
    ent = EntityExtractor()
    ioc = IOCExtractor()

    try:
        if args.watch:
            run_watch(store, ioc, ent, args.batch, args.interval)
        else:
            run_once(store, ioc, ent, args.batch)
    except KeyboardInterrupt:
        log.info("interrupted")
        return 130
    finally:
        store.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
