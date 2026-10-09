"""Search engines for Discover: local DB index (FTS5) + real dark-web engines over Tor (read-only)."""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

# Matches a v3 .onion address (56 characters)
ONION_RE = re.compile(r"\b([a-z2-7]{56})\.onion\b", re.IGNORECASE)
TOR_PROXY = os.environ.get("SENTINELX_TOR_PROXY", "socks5://127.0.0.1:9050")
TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=60.0)


# One search result (same shape for every engine)
@dataclass
class Result:
    engine: str
    title: str
    url: str
    snippet: str = ""
    onion: str | None = None
    post_id: int | None = None      # set when the result is already in the corpus
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Local index (FTS5 over raw_posts)
# --------------------------------------------------------------------------- #

def _fts_query(q: str) -> str:
    """Make user text a safe FTS5 query (quoted terms, OR'd, prefix match)."""
    terms = [t for t in re.findall(r"[\w$.@:-]+", q.lower()) if len(t) > 1]
    return " OR ".join(f'"{t.replace(chr(34), "")}"*' for t in terms[:12])


# Searches posts we have already collected (no network needed)
class LocalIndexEngine:
    name = "local"
    label = "SentinelX index"
    needs_tor = False

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def search(self, query: str, limit: int = 20) -> list[Result]:
        # Turn the query into FTS syntax; nothing usable -> no results
        fq = _fts_query(query)
        if not fq:
            return []
        # Full-text search; snippet() gives a text preview with the match marked «like this»
        rows = self.conn.execute(
            """
            SELECT rp.id, rp.thread_title, rp.source, rp.category,
                   snippet(posts_fts, 1, '«', '»', ' … ', 18) AS snip,
                   bm25(posts_fts) AS rank
            FROM posts_fts
            JOIN raw_posts rp ON rp.id = posts_fts.rowid
            WHERE posts_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fq, limit),
        ).fetchall()
        # Turn each row into a Result
        out = []
        for r in rows:
            onion = r["source"] if str(r["source"]).endswith(".onion") else None
            out.append(Result(
                engine=self.name,
                title=r["thread_title"],
                url=f"sentinelx://post/{r['id']}",
                snippet=r["snip"],
                onion=onion,
                post_id=r["id"],
                score=round(-float(r["rank"]), 3),   # bm25: lower is better
            ))
        return out


# --------------------------------------------------------------------------- #
# Real engines over Tor
# --------------------------------------------------------------------------- #

# Config for one dark-web search engine
@dataclass(frozen=True)
class EngineSpec:
    name: str
    label: str
    url_template: str   # {q} is replaced with the URL-encoded query


# Dark-web engines to query; add more here (result parsing is generic)
ENGINES: list[EngineSpec] = [
    EngineSpec(
        "ahmia",
        "Ahmia (over Tor)",
        "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/?q={q}",
    ),
]


def _onion_target(href: str) -> str | None:
    """The .onion URL a result link points to, unwrapping redirect params."""
    # Direct .onion link?
    if not href:
        return None
    if ONION_RE.search(urlparse(href).netloc or ""):
        return href
    # Engines often link via /redirect?url=<target> or ?redirect_url=<target>.
    for vals in parse_qs(urlparse(href).query).values():
        for v in vals:
            v = unquote(v)
            if ONION_RE.search(urlparse(v).netloc or ""):
                return v
    return None


def parse_results(html: str, engine: str, own_host: str = "") -> list[Result]:
    """Generic result-page parser: every outbound .onion link is a candidate."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out: list[Result] = []
    for a in soup.find_all("a", href=True):
        target = _onion_target(a["href"])
        if not target:
            continue
        # Skip links back to the engine itself and duplicates
        host = urlparse(target).netloc.lower()
        if host == own_host or target in seen:
            continue
        seen.add(target)
        # Snippet: the text of the closest block around the link.
        block = a.find_parent(["li", "div", "article", "p"])
        snippet = block.get_text(" ", strip=True) if block else ""
        title = a.get_text(" ", strip=True) or host
        out.append(Result(engine=engine, title=title[:200], url=target, snippet=snippet[:300], onion=host))
    return out


def _form_token(c: httpx.Client, search_url: str) -> str:
    """Fetch the hidden anti-bot token some engines (Ahmia) need on the search URL."""
    parts = urlparse(search_url)
    try:
        home = c.get(f"{parts.scheme}://{parts.netloc}/")
        form = BeautifulSoup(home.text, "html.parser").find("form")
    except httpx.HTTPError:
        return ""
    if form is None:
        return ""
    # Copy every hidden <input> from the engine's search form
    fields = [
        (i.get("name"), i.get("value", ""))
        for i in form.find_all("input", type="hidden")
        if i.get("name")
    ]
    return "".join(f"&{quote_plus(n)}={quote_plus(v)}" for n, v in fields)


# Searches a real dark-web engine through Tor
class OnionSearchEngine:
    needs_tor = True

    def __init__(self, spec: EngineSpec, proxy: str = TOR_PROXY) -> None:
        self.spec = spec
        self.name = spec.name
        self.label = spec.label
        self.proxy = proxy

    def search(self, query: str, limit: int = 20) -> list[Result]:
        # Build the search URL, add the anti-bot token, fetch results over Tor
        url = self.spec.url_template.format(q=quote_plus(query))
        with httpx.Client(proxy=self.proxy, timeout=TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"}) as c:
            url += _form_token(c, url)
            r = c.get(url)
            r.raise_for_status()
        # Parse every .onion link on the results page
        own = urlparse(url).netloc.lower()
        return parse_results(r.text, self.name, own_host=own)[:limit]
