"""Generic page reader: turn ANY fetched HTML page into one SentinelX post.

The forum scrapers understand two specific layouts (DarkBay's JSON API and
SilkVault's markup). Pages found through search can be anything: a market
listing, a paste, a leak site, a blog. This reader makes no layout
assumptions: it strips non-content elements, keeps the title and the readable
text, and returns a post dict in the same shape the scrapers produce, so
`Store.insert_posts` and every downstream stage work unchanged.

Identity: source_post_id is a stable 48-bit hash of the normalised URL, so
re-reading the same page dedupes via UNIQUE(source, source_post_id).
"""

from __future__ import annotations

import hashlib
import re
import time
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup

# Elements that are never the content of a page.
_DROP = ["script", "style", "noscript", "svg", "iframe", "form", "nav", "header", "footer", "aside"]
MAX_BODY_CHARS = 20_000
MIN_BODY_CHARS = 40


def normalise_url(url: str) -> str:
    """Scheme-less-safe, fragment-free, trailing-slash-normalised URL."""
    if not re.match(r"^https?://", url):
        url = "http://" + url
    u = urlparse(url.strip())
    path = u.path.rstrip("/") or "/"
    return urlunparse((u.scheme.lower(), u.netloc.lower(), path, "", u.query, ""))


def url_id(url: str) -> int:
    """Stable 48-bit id for a URL (fits SQLite INTEGER, deterministic across runs)."""
    return int(hashlib.sha256(normalise_url(url).encode()).hexdigest()[:12], 16)


def read_page(html: str, url: str, fetched_at: float | None = None) -> dict | None:
    """Extract one post from a page. Returns None when there's no real text."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_DROP):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    h1 = soup.find("h1")
    if not title and h1:
        title = h1.get_text(" ", strip=True)

    # Prefer the densest content container; fall back to the whole body.
    container = soup.find("main") or soup.find("article") or soup.body or soup
    blocks = [
        b.get_text(" ", strip=True)
        for b in container.find_all(["p", "li", "pre", "td", "div", "h2", "h3"])
        if not b.find(["p", "li", "pre", "div"])  # leaf-ish blocks only, avoid duplicates
    ]
    text = "\n".join(t for t in blocks if len(t) > 1) or container.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_BODY_CHARS]
    if len(text) < MIN_BODY_CHARS:
        return None

    host = urlparse(normalise_url(url)).netloc
    pid = url_id(url)
    return {
        "id": pid,
        "thread_id": pid,
        "thread_title": (title or host)[:300],
        "category": "discovered",
        "author": "unknown",
        "body": text,
        # A search result has no reliable post date; record when we read it.
        "created_at": fetched_at if fetched_at is not None else time.time(),
        "source": host,
        "url": normalise_url(url),
    }
