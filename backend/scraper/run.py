"""Scraper entrypoint.

Pulls posts from the synthetic .onion forum through Tor's SOCKS5 proxy,
deduplicates them by source post id, and persists them into the local SQLite
store at backend/db/sentinelx.db.

Usage:
    python -m backend.scraper.run --once
    python -m backend.scraper.run --watch --interval 30
    python -m backend.scraper.run --reset-cursor
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import httpx

from backend.db.store import Store
from backend.scraper.client import ForumClient, read_onion_hostname

log = logging.getLogger("sentinelx.scraper")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def poll_once(store: Store, client: ForumClient, batch_limit: int = 1000) -> tuple[int, int, int]:
    """One pass: fetch new posts since the stored cursor, insert, return counts.

    Returns (fetched, inserted, duplicates).
    """
    cursor_before = store.get_cursor()
    log.info("polling /api/posts since=%s", cursor_before)

    with store.run(cursor_before) as h:
        try:
            payload = client.fetch_posts(since=cursor_before, limit=batch_limit)
        except httpx.HTTPError as e:
            log.error("fetch failed: %s", e)
            h.error = repr(e)
            raise

        posts = payload.get("posts", [])
        h.fetched = len(posts)

        inserted, duplicates = store.insert_posts(posts)
        h.inserted = inserted
        h.duplicates = duplicates
        h.cursor_after = store.get_cursor()

        log.info(
            "fetched=%d inserted=%d duplicates=%d cursor=%s -> %s total_rows=%d",
            h.fetched, h.inserted, h.duplicates,
            cursor_before, h.cursor_after, store.count_posts(),
        )
        return h.fetched, h.inserted, h.duplicates


def run_watch(store: Store, client: ForumClient, interval: float) -> None:
    log.info("watch mode: polling every %.1fs (Ctrl-C to stop)", interval)
    while True:
        try:
            poll_once(store, client)
        except Exception as e:
            log.warning("poll errored, will retry: %s", e)
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sentinelx-scraper")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="poll once and exit (default)")
    mode.add_argument("--watch", action="store_true", help="poll in a loop")
    mode.add_argument("--reset-cursor", action="store_true",
                      help="wipe raw_posts + scraper_runs (forces full re-scrape next run)")

    p.add_argument("--interval", type=float, default=30.0, help="watch interval in seconds")
    p.add_argument("--limit", type=int, default=1000, help="max rows per fetch")
    p.add_argument("--proxy", default="socks5://127.0.0.1:9050",
                   help="SOCKS5 proxy URL. httpx's SOCKS5 transport does proxy-side DNS by default, "
                        "so plain socks5:// works for .onion (do NOT use socks5h:// — httpx rejects it).")
    p.add_argument("--onion", default=None,
                   help="override .onion hostname (default: read from tor_config/hidden_service/hostname)")
    p.add_argument("--db", default=None, help="override DB path")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    _setup_logging(args.verbose)

    store = Store(args.db) if args.db else Store()

    if args.reset_cursor:
        store.reset()
        log.info("cursor reset: raw_posts and scraper_runs cleared")
        store.close()
        return 0

    onion = args.onion or read_onion_hostname()
    log.info("target: http://%s  via %s", onion, args.proxy)

    with ForumClient(onion=onion, proxy=args.proxy) as client:
        try:
            if args.watch:
                run_watch(store, client, args.interval)
            else:
                poll_once(store, client, batch_limit=args.limit)
        except KeyboardInterrupt:
            log.info("interrupted")
            return 130
        except httpx.HTTPError as e:
            log.error("scrape failed: %s", e)
            return 1
        finally:
            store.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
