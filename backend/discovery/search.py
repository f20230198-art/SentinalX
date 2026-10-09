"""Run a discovery search: optional LLM query rewrite -> query engines -> rank results."""

from __future__ import annotations

import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from backend.discovery.engines import ENGINES, LocalIndexEngine, OnionSearchEngine, Result

log = logging.getLogger("sentinelx.discovery")

# Words that suggest a result is threat-related (gives a small ranking boost)
THREAT_TERMS = {
    "access", "rdp", "vpn", "dump", "leak", "combo", "credentials", "exploit",
    "cve", "ransomware", "loader", "stealer", "botnet", "phishing", "breach",
    "database", "fullz", "admin", "shell", "0day", "zero-day", "c2",
}


# Engines the UI can offer
def available_engines() -> list[dict]:
    return [{"name": "local", "label": LocalIndexEngine.label, "needs_tor": False}] + [
        {"name": e.name, "label": e.label, "needs_tor": True} for e in ENGINES
    ]


def refine_query(query: str, ollama_url: str = "http://127.0.0.1:11434") -> tuple[str, str | None]:
    """Ask the local LLM for ≤6 search keywords. Returns (query, error)."""
    from backend.llm.client import OllamaClient, OllamaError

    # Ask the LLM to turn a question into a few keywords
    prompt = (
        "Rewrite this threat-intelligence question as 3 to 6 short search keywords "
        "for a dark-web search engine. Reply with the keywords only, space-separated, "
        "no punctuation, no explanation.\n\nQuestion: " + query[:500]
    )
    try:
        with OllamaClient(base_url=ollama_url) as c:
            out = c.generate(prompt, temperature=0.1, num_predict=30).text
    except OllamaError as e:
        return query, f"LLM unavailable, used your query as typed ({e})"
    # Keep at most 6 clean words
    words = re.findall(r"[\w.@$-]+", out)[:6]
    return (" ".join(words) or query), None


# Score = query words found (x2) + threat words (x0.5) + local search score
def _score(r: Result, terms: set[str]) -> float:
    text = f"{r.title} {r.snippet}".lower()
    hits = sum(1 for t in terms if t in text)
    vocab = sum(1 for t in THREAT_TERMS if t in text)
    base = r.score if r.engine == "local" else 0.0
    return round(hits * 2 + vocab * 0.5 + base, 3)


def run_search(
    conn: sqlite3.Connection,
    query: str,
    engines: list[str] | None = None,
    refine: bool = False,
    limit: int = 25,
) -> dict:
    # Optionally let the LLM rewrite the query first
    query = query.strip()
    refined, refine_error = (refine_query(query) if refine else (query, None))
    wanted = set(engines or ["local"])

    # Build the list of engines the user picked
    jobs: list[tuple[str, object]] = []
    if "local" in wanted:
        jobs.append(("local", LocalIndexEngine(conn)))
    for spec in ENGINES:
        if spec.name in wanted:
            jobs.append((spec.name, OnionSearchEngine(spec)))

    results: list[Result] = []
    errors: dict[str, str] = {}

    # Local search runs here; network engines run in parallel threads
    remote = [(n, e) for n, e in jobs if n != "local"]
    with ThreadPoolExecutor(max_workers=max(1, len(remote))) as pool:
        futures = {n: pool.submit(e.search, refined, limit) for n, e in remote}
        for n, e in jobs:
            if n == "local":
                try:
                    results += e.search(refined, limit)
                except sqlite3.Error as ex:
                    errors[n] = f"index error: {ex}"
        for n, f in futures.items():
            try:
                results += f.result()
            except Exception as ex:  # noqa: BLE001 — one engine must not fail the search
                log.warning("engine %s failed: %s", n, ex)
                errors[n] = _friendly(ex)

    # Score every result against the query words
    terms = {t for t in re.findall(r"\w+", refined.lower()) if len(t) > 2}
    for r in results:
        r.score = _score(r, terms)

    # Mark remote results whose host we have already ingested.
    if results:
        known = {row[0] for row in conn.execute("SELECT DISTINCT source FROM raw_posts")}
        for r in results:
            if r.engine != "local" and r.onion in known:
                r.snippet = (r.snippet + " · already in corpus").strip(" ·")

    # Local results first, then by score
    results.sort(key=lambda r: (r.engine != "local", -r.score))
    return {
        "query": query,
        "refined": refined if refined != query else None,
        "refine_error": refine_error,
        "results": [r.to_dict() for r in results],
        "errors": errors,
    }


# Turn common network errors into a readable message
def _friendly(ex: Exception) -> str:
    s = repr(ex)
    if "ConnectError" in s or "ProxyError" in s or "SOCKS" in s.upper():
        return "Tor proxy not reachable (start it with `docker compose up -d`)."
    if "Timeout" in s:
        return "The engine timed out over Tor. Try again; onion search is slow."
    return s[:200]
