"""HTML scraper for forums with no API (SilkVault layout), parsed with BeautifulSoup over Tor."""

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

# Legacy per-forum id offset; kept so existing rows keep the same ids
ID_BLOCK_SIZE = 1_000_000_000


def _source_id_offset(source: str) -> int:
    """Stable id offset for a forum label."""
    # Stable hash (Python's hash() is salted per-process; sum of bytes is not).
    # Turn the forum name into a fixed number 0-999
    h = sum(source.encode("utf-8")) % 1000
    # +1 so DarkBay's un-offset block [0, 1e9) is never reused by an HTML forum.
    return (h + 1) * ID_BLOCK_SIZE

# Where SilkVault's Tor writes its .onion hostname on the host.
SILKVAULT_HOSTNAME_FILE = (
    Path(__file__).resolve().parents[2]
    / "darknet" / "silkvault" / "tor" / "hidden_service" / "hostname"
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


# Results of one crawl: posts found, pages visited, errors
@dataclass
class HtmlScrapeResult:
    """What one full HTML crawl produced."""
    posts: list[dict] = field(default_factory=list)
    listings_seen: int = 0
    pages_fetched: int = 0
    errors: list[str] = field(default_factory=list)


def _epoch_from_message(msg: Any) -> float | None:
    """Get the message timestamp (data-epoch or <time>); None if missing."""
    # Prefer the data-epoch attribute
    raw = msg.get("data-epoch")
    if raw:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    # Otherwise read the <time datetime="..."> tag
    time_tag = msg.find("time")
    if time_tag and time_tag.get("datetime"):
        iso = time_tag["datetime"].strip()
        try:
            return datetime.fromisoformat(iso).timestamp()
        except ValueError:
            pass
    return None


def _int_attr(node: Any, *names: str) -> int | None:
    # Try each attribute name and return the first one that is a number
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
        # proxy=None = no Tor (used in tests)
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

    # Download one page and return its HTML
    def _get(self, path_or_url: str) -> str:
        url = urljoin(self.base_url + "/", path_or_url.lstrip("/"))
        r = self._client.get(url)
        r.raise_for_status()
        return r.text

    # --- parsing ---------------------------------------------------------- #

    def _listing_urls(self, index_html: str) -> list[str]:
        """Extract distinct /listing/<id> links from an index or board page."""
        # Normal case: links on listing cards
        soup = BeautifulSoup(index_html, "html.parser")
        urls: list[str] = []
        seen: set[str] = set()
        for a in soup.select("a.listing-card[href]"):
            href = a["href"].strip()
            if href and href not in seen:
                seen.add(href)
                urls.append(href)
        # Fallback: any link to /listing/N
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

        # Title from the <h1>
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

        # One post per message: id, time, author, body (skip incomplete ones)
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
        """Crawl index -> listings -> messages, keeping only messages newer than `since`."""
        # Each forum gets its own id range
        result = HtmlScrapeResult()
        offset = _source_id_offset(source or self.host)

        # Step 1: load the front page
        try:
            index_html = self._get("/")
            result.pages_fetched += 1
        except httpx.HTTPError as e:
            result.errors.append(f"index fetch failed: {e!r}")
            return result

        # Step 2: find all listing links (capped)
        listing_urls = self._listing_urls(index_html)
        if len(listing_urls) > self.max_listings:
            log.warning("forum has %d listings, capping at %d",
                        len(listing_urls), self.max_listings)
            listing_urls = listing_urls[: self.max_listings]
        result.listings_seen = len(listing_urls)

        # Step 3: open each listing and collect messages newer than `since`
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

        # Oldest first
        result.posts.sort(key=lambda p: p["created_at"])
        log.info("crawl done: %d listings, %d pages, %d new posts, %d errors",
                 result.listings_seen, result.pages_fetched,
                 len(result.posts), len(result.errors))
        return result
