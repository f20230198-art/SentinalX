"""Generic HTML scraper for darknet forums that have no JSON API.

DarkBay (the original synthetic forum) exposes a convenient /api/posts JSON
endpoint. Real darknet forums do not — and neither does SilkVault, our second
synthetic forum, on purpose. This module is the scraper path for those: it
fetches HTML pages over Tor and parses them with BeautifulSoup into the same
post-dict shape the JSON scraper produces, so backend.db.store.Store can ingest
either source unchanged.

It targets the SilkVault page structure:
    index / board page  -> <a class="listing-card" data-listing-id="..." href="/listing/N">
    listing page        -> <h1> title, vendor in .page-sub,
                            <div class="vault-message" data-message-id="N"
                                 data-epoch="...">
                              <span class="message-author">  <div class="message-body">

Design notes:
  * Parsing leans on the data-* attributes first (data-listing-id,
    data-message-id, data-epoch) and falls back to class names / text. This is
    a real HTML parser, not a regex — it tolerates whitespace and attribute
    reordering the way a parser of an unfamiliar forum must.
  * Each message becomes one post. source_thread_id = listing id,
    source_post_id = message id, both taken straight from the forum's own ids.
  * The crawl is bounded: a page cap and a per-request timeout keep a
    misbehaving or huge forum from hanging the scraper.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("sentinelx.scraper.html")

# Tor circuits are slow — generous timeouts, same posture as the JSON client.
DEFAULT_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=60.0)
DEFAULT_SOCKS_PROXY = "socks5://127.0.0.1:9051"

# Safety caps so a hostile or huge forum can't run the scraper forever.
DEFAULT_MAX_LISTINGS = 500

# raw_posts.source_post_id is globally UNIQUE, but each forum numbers its own
# posts from 1 — so DarkBay post #30 and SilkVault message #30 would collide.
# The HTML scraper offsets every id into a forum-specific block keyed by a hash
# of the source label, well clear of DarkBay's low ids. 1e9 spacing per block
# leaves room for a billion posts per forum before two blocks could ever meet.
ID_BLOCK_SIZE = 1_000_000_000


def _source_id_offset(source: str) -> int:
    """Deterministic per-forum offset for source_post_id / source_thread_id.

    Same `source` label always yields the same offset, so re-crawling a forum
    keeps its rows stable (the UNIQUE dedup still works on re-runs). Different
    forums get different blocks, so their ids never collide.
    """
    # Stable hash (Python's hash() is salted per-process; sum of bytes is not).
    h = sum(source.encode("utf-8")) % 1000
    # +1 so DarkBay's un-offset block [0, 1e9) is never reused by an HTML forum.
    return (h + 1) * ID_BLOCK_SIZE

# Where SilkVault's Tor writes its .onion hostname on the host.
SILKVAULT_HOSTNAME_FILE = (
    Path(__file__).resolve().parents[2]
    / "tor_config_silkvault" / "hidden_service" / "hostname"
)


def read_silkvault_hostname(path: Path = SILKVAULT_HOSTNAME_FILE) -> str:
    """Read SilkVault's .onion address from its Tor hidden-service dir."""
    if not path.exists():
        raise FileNotFoundError(
            f"SilkVault hidden-service hostname not found at {path}. "
            "Is the SilkVault stack running? "
            "`docker compose up -d silkvault tor-sv`."
        )
    addr = path.read_text(encoding="utf-8").strip()
    if not addr.endswith(".onion"):
        raise ValueError(f"hostname file did not contain a .onion address: {addr!r}")
    return addr


@dataclass
class HtmlScrapeResult:
    """What one full HTML crawl produced."""
    posts: list[dict] = field(default_factory=list)
    listings_seen: int = 0
    pages_fetched: int = 0
    errors: list[str] = field(default_factory=list)


def _epoch_from_message(msg: Any) -> float | None:
    """Pull a Unix epoch out of a vault-message node.

    Prefers the explicit data-epoch attribute; falls back to parsing the ISO
    string in <time datetime="...">. Returns None if neither is usable — the
    caller drops messages with no timestamp (the cursor needs one).
    """
    raw = msg.get("data-epoch")
    if raw:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    time_tag = msg.find("time")
    if time_tag and time_tag.get("datetime"):
        iso = time_tag["datetime"].strip()
        try:
            return datetime.fromisoformat(iso).timestamp()
        except ValueError:
            pass
    return None


def _int_attr(node: Any, *names: str) -> int | None:
    """First parseable integer among the named attributes of `node`."""
    for n in names:
        v = node.get(n)
        if v is None:
            continue
        try:
            return int(str(v).strip())
        except ValueError:
            continue
    return None


class HtmlForumClient:
    """Crawl a SilkVault-structured HTML forum over Tor (or plain HTTP)."""

    def __init__(
        self,
        base_url: str,
        proxy: str | None = DEFAULT_SOCKS_PROXY,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        max_listings: int = DEFAULT_MAX_LISTINGS,
    ) -> None:
        # Accept a bare host, a host with scheme, or a full URL.
        if not re.match(r"^https?://", base_url):
            base_url = f"http://{base_url}"
        self.base_url = base_url.rstrip("/")
        self.host = urlparse(self.base_url).netloc or self.base_url
        self.max_listings = max_listings
        # proxy=None lets the caller scrape a plain-HTTP forum without Tor
        # (used by tests); .onion targets need the SOCKS5 proxy.
        self._client = httpx.Client(
            proxy=proxy, timeout=timeout, follow_redirects=True
        )

    def __enter__(self) -> "HtmlForumClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # --- fetching --------------------------------------------------------- #

    def _get(self, path_or_url: str) -> str:
        url = urljoin(self.base_url + "/", path_or_url.lstrip("/"))
        r = self._client.get(url)
        r.raise_for_status()
        return r.text

    # --- parsing ---------------------------------------------------------- #

    def _listing_urls(self, index_html: str) -> list[str]:
        """Extract distinct /listing/<id> links from an index or board page."""
        soup = BeautifulSoup(index_html, "html.parser")
        urls: list[str] = []
        seen: set[str] = set()
        for a in soup.select("a.listing-card[href]"):
            href = a["href"].strip()
            if href and href not in seen:
                seen.add(href)
                urls.append(href)
        # Fallback: any anchor pointing at /listing/N, in case the card class
        # name differs on a forum that is SilkVault-like but not identical.
        if not urls:
            for a in soup.find_all("a", href=re.compile(r"/listing/\d+")):
                href = a["href"].strip()
                if href not in seen:
                    seen.add(href)
                    urls.append(href)
        return urls

    def _parse_listing(self, listing_html: str, listing_url: str) -> list[dict]:
        """Turn one listing page into a list of post dicts (one per message)."""
        soup = BeautifulSoup(listing_html, "html.parser")

        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else "(untitled listing)"

        # Board + vendor live in the .page-sub line; best-effort extraction.
        board = "unknown"
        sub = soup.select_one(".page-sub")
        if sub:
            board_link = sub.find("a", href=re.compile(r"/board/"))
            if board_link:
                board = board_link.get_text(strip=True)

        # listing id from the URL, e.g. /listing/42 -> 42
        m = re.search(r"/listing/(\d+)", listing_url)
        listing_id = int(m.group(1)) if m else 0

        posts: list[dict] = []
        for msg in soup.select("div.vault-message"):
            msg_id = _int_attr(msg, "data-message-id")
            if msg_id is None:
                continue
            epoch = _epoch_from_message(msg)
            if epoch is None:
                continue
            author_tag = msg.select_one(".message-author")
            body_tag = msg.select_one(".message-body")
            author = author_tag.get_text(strip=True) if author_tag else "unknown"
            body = body_tag.get_text("\n", strip=True) if body_tag else ""
            if not body:
                continue
            posts.append({
                "id": msg_id,
                "thread_id": listing_id,
                "thread_title": title,
                "category": board,
                "author": author,
                "body": body,
                "created_at": epoch,
            })
        return posts

    # --- public crawl ----------------------------------------------------- #

    def crawl(self, since: float = 0.0, source: str | None = None) -> HtmlScrapeResult:
        """Crawl the whole forum: index -> every listing -> every message.

        `since` filters out messages at or before that epoch, matching the
        JSON scraper's incremental-cursor behaviour. The forum has no
        server-side `since`, so filtering happens client-side after parsing.

        `source` (default: the forum host) keys a per-forum id offset applied
        to every post's id / thread_id, so SilkVault's message #30 cannot
        collide with DarkBay's post #30 in the globally-UNIQUE source_post_id.
        """
        result = HtmlScrapeResult()
        offset = _source_id_offset(source or self.host)

        try:
            index_html = self._get("/")
            result.pages_fetched += 1
        except httpx.HTTPError as e:
            result.errors.append(f"index fetch failed: {e!r}")
            return result

        listing_urls = self._listing_urls(index_html)
        if len(listing_urls) > self.max_listings:
            log.warning("forum has %d listings, capping at %d",
                        len(listing_urls), self.max_listings)
            listing_urls = listing_urls[: self.max_listings]
        result.listings_seen = len(listing_urls)

        for url in listing_urls:
            try:
                html = self._get(url)
                result.pages_fetched += 1
            except httpx.HTTPError as e:
                result.errors.append(f"{url} fetch failed: {e!r}")
                continue
            try:
                for post in self._parse_listing(html, url):
                    if post["created_at"] > since:
                        # Namespace ids into this forum's block.
                        post["id"] += offset
                        post["thread_id"] += offset
                        result.posts.append(post)
            except Exception as e:  # noqa: BLE001 — one bad page must not kill the crawl
                result.errors.append(f"{url} parse failed: {e!r}")

        result.posts.sort(key=lambda p: p["created_at"])
        log.info("crawl done: %d listings, %d pages, %d new posts, %d errors",
                 result.listings_seen, result.pages_fetched,
                 len(result.posts), len(result.errors))
        return result
