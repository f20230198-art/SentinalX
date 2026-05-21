"""Scraper entrypoint.

Pulls posts from a synthetic .onion forum through Tor's SOCKS5 proxy,
deduplicates them by source post id, and persists them into the local SQLite
store at backend/db/sentinelx.db.

Two scraper paths:
  * JSON  (default) — the DarkBay forum exposes /api/posts; fast and clean.
  * HTML  (--html)  — for forums with no API (SilkVault, real darknet forums);
                      crawls and parses HTML pages with BeautifulSoup.

Usage:
    python -m backend.scraper.run --once
    python -m backend.scraper.run --watch --interval 30
    python -m backend.scraper.run --reset-cursor

    # HTML mode — point at any SilkVault-structured forum:
    python -m backend.scraper.run --once --html \\
        --url <onion>.onion --proxy socks5://127.0.0.1:9051 --source silkvault
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import httpx

from backend.db.store import Store
from backend.scraper.client import ForumClient, read_onion_hostname
from backend.scraper.html_client import HtmlForumClient

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


def poll_html_once(
    store: Store, client: HtmlForumClient, source: str
) -> tuple[int, int, int]:
    """One pass of the HTML scraper: crawl the forum, insert new posts.

    Mirrors poll_once but for forums with no JSON API. `source` is recorded on
    every inserted row so the DB knows which forum the post came from.
    Returns (fetched, inserted, duplicates).
    """
    cursor_before = store.get_cursor()
    log.info("HTML crawl of %s since=%s", client.host, cursor_before)

    with store.run(cursor_before) as h:
        try:
            crawl = client.crawl(since=cursor_before, source=source)
        except httpx.HTTPError as e:
            log.error("crawl failed: %s", e)
            h.error = repr(e)
            raise

        if crawl.errors:
            # A crawl can partly succeed — log issues but keep the good posts.
            log.warning("crawl reported %d error(s): %s",
                        len(crawl.errors), "; ".join(crawl.errors[:3]))

        h.fetched = len(crawl.posts)
        inserted, duplicates = store.insert_posts(crawl.posts, source=source)
        h.inserted = inserted
        h.duplicates = duplicates
        h.cursor_after = store.get_cursor()

        log.info(
            "crawled listings=%d pages=%d fetched=%d inserted=%d duplicates=%d "
            "cursor=%s -> %s total_rows=%d",
            crawl.listings_seen, crawl.pages_fetched, h.fetched, h.inserted,
            h.duplicates, cursor_before, h.cursor_after, store.count_posts(),
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
                        "so plain socks5:// works for .onion (do NOT use socks5h:// — httpx rejects it). "
                        "SilkVault's Tor is on socks5://127.0.0.1:9051.")
    p.add_argument("--onion", default=None,
                   help="override .onion hostname (default: read from tor_config/hidden_service/hostname)")
    p.add_argument("--db", default=None, help="override DB path")
    p.add_argument("-v", "--verbose", action="store_true")

    # --- HTML-scraper mode (forums with no JSON API) --- #
    p.add_argument("--html", action="store_true",
                   help="use the generic HTML scraper instead of the JSON API")
    p.add_argument("--url", default=None,
                   help="forum base URL or .onion host for --html mode "
                        "(default: read from tor_config_silkvault/hidden_service/hostname)")
    p.add_argument("--source", default=None,
                   help="label recorded on every scraped row identifying the forum "
                        "(--html default: the forum host)")
    args = p.parse_args(argv)

    _setup_logging(args.verbose)

    store = Store(args.db) if args.db else Store()

    if args.reset_cursor:
        store.reset()
        log.info("cursor reset: raw_posts and scraper_runs cleared")
        store.close()
        return 0

    # --- HTML scraper path (forums with no JSON API) --- #
    if args.html:
        from backend.scraper.html_client import (
            HtmlForumClient,
            read_silkvault_hostname,
        )

        url = args.url or read_silkvault_hostname()
        # SilkVault's Tor is on :9051; nudge the default if the user left
        # --proxy at DarkBay's :9050 and didn't pass --url explicitly.
        proxy = args.proxy
        if not args.url and proxy == "socks5://127.0.0.1:9050":
            proxy = "socks5://127.0.0.1:9051"
        source = args.source or HtmlForumClient(url, proxy=None).host
        log.info("HTML target: %s  via %s  (source=%s)", url, proxy, source)

        with HtmlForumClient(url, proxy=proxy) as client:
            try:
                if args.watch:
                    log.info("watch mode: crawling every %.1fs (Ctrl-C to stop)",
                             args.interval)
                    while True:
                        try:
                            poll_html_once(store, client, source)
                        except Exception as e:  # noqa: BLE001
                            log.warning("crawl errored, will retry: %s", e)
                        time.sleep(args.interval)
                else:
                    poll_html_once(store, client, source)
            except KeyboardInterrupt:
                log.info("interrupted")
                return 130
            except httpx.HTTPError as e:
                log.error("scrape failed: %s", e)
                return 1
            finally:
                store.close()
        return 0

    # --- JSON scraper path (default — the DarkBay forum) --- #
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
