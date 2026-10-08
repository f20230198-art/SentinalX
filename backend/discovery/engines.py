"""Search engines for discovery.

Two kinds, one interface (`search(query, limit) -> list[Result]`):

* LocalIndexEngine — full-text search (SQLite FTS5) over everything SentinelX
  has already collected. This is exactly what a dark-web search engine like
  Ahmia is: an index of crawled pages. It needs no network, so a demo never
  fails, and it answers "have we already seen this?" first.

* OnionSearchEngine — a real dark-web search engine queried over Tor (SOCKS5).
  Results pages differ per engine, so parsing is deliberately generic: every
  link that points at a .onion address (directly or through a redirect
  parameter) is a candidate, titled by its anchor text. Engines are config,
  not code: add one by appending to ENGINES.

Safety posture: read-only GETs, no forms submitted, no logins, nothing
downloaded or executed; results are only fetched when an analyst sends them
to the pipeline.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

ONION_RE = re.compile(r"\b([a-z2-7]{56})\.onion\b", re.IGNORECASE)
TOR_PROXY = os.environ.get("SENTINELX_TOR_PROXY", "socks5://127.0.0.1:9050")
TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=60.0)


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
    """Turn free text into a safe FTS5 query: quoted terms, OR'd, prefix-matched.

    Quoting every term neutralises FTS operators in user input (AND, NEAR,
    column filters, unbalanced quotes), so arbitrary text can't break the query.
    """
    terms = [t for t in re.findall(r"[\w$.@:-]+", q.lower()) if len(t) > 1]
    return " OR ".join(f'"{t.replace(chr(34), "")}"*' for t in terms[:12])


class LocalIndexEngine:
    name = "local"
    label = "SentinelX index"
    needs_tor = False

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def search(self, query: str, limit: int = 20) -> list[Result]:
        fq = _fts_query(query)
        if not fq:
            return []
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

@dataclass(frozen=True)
class EngineSpec:
    name: str
    label: str
    url_template: str   # {q} is replaced with the URL-encoded query


# Ahmia's onion service (also reachable at ahmia.fi). Add more engines here;
# parsing is generic so most result pages work without new code.
ENGINES: list[EngineSpec] = [
    EngineSpec(
        "ahmia",
        "Ahmia (over Tor)",
        "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/?q={q}",
    ),
]


def _onion_target(href: str) -> str | None:
    """The .onion URL a result link points to, unwrapping redirect params."""
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


class OnionSearchEngine:
    needs_tor = True

    def __init__(self, spec: EngineSpec, proxy: str = TOR_PROXY) -> None:
        self.spec = spec
        self.name = spec.name
        self.label = spec.label
        self.proxy = proxy

    def search(self, query: str, limit: int = 20) -> list[Result]:
        url = self.spec.url_template.format(q=quote_plus(query))
        with httpx.Client(proxy=self.proxy, timeout=TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = c.get(url)
            r.raise_for_status()
        own = urlparse(url).netloc.lower()
        return parse_results(r.text, self.name, own_host=own)[:limit]
