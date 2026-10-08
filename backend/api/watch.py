"""Watchlists: saved terms that turn matching posts into alerts.

Matching runs on read (`check_all` is called by the API before it lists
watchlists or alerts) and is incremental per watchlist via `last_post_id`,
so it stays cheap as the corpus grows. A term matches as an exact phrase in
a post's title, body or English translation (FTS5), case-insensitive.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any

MAX_TERMS = 25


def _clean_terms(terms: list[Any]) -> list[str]:
    out: list[str] = []
    for t in terms:
        t = re.sub(r"\s+", " ", str(t)).strip()
        if 2 <= len(t) <= 100 and t.lower() not in (x.lower() for x in out):
            out.append(t)
    return out[:MAX_TERMS]


def _phrase(term: str) -> str:
    """Exact-phrase FTS5 query for one term (quotes escaped by doubling)."""
    words = re.findall(r"[\w$.@:-]+", term)
    return '"' + " ".join(words).replace('"', '""') + '"' if words else ""


def _match_terms(text: str, terms: list[str]) -> list[str]:
    low = text.lower()
    return [t for t in terms if t.lower() in low]


def check(conn: sqlite3.Connection, wl: sqlite3.Row) -> int:
    """Scan posts newer than this watchlist's cursor; record hits. Returns new hits."""
    terms = json.loads(wl["terms_json"])
    queries = [q for q in (_phrase(t) for t in terms) if q]
    max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM raw_posts").fetchone()[0]
    if not queries or max_id <= wl["last_post_id"]:
        return 0
    rows = conn.execute(
        """
        SELECT rp.id, rp.thread_title, rp.body, COALESCE(rp.body_en, '') AS body_en
        FROM posts_fts JOIN raw_posts rp ON rp.id = posts_fts.rowid
        WHERE posts_fts MATCH ? AND rp.id > ?
        """,
        (" OR ".join(queries), wl["last_post_id"]),
    ).fetchall()
    now, new = time.time(), 0
    for r in rows:
        # FTS tokenisation can match "acme-corp" for "acme corp"; report the
        # literal terms found when possible, else the first term.
        matched = _match_terms(f"{r['thread_title']}\n{r['body']}\n{r['body_en']}", terms) or terms[:1]
        cur = conn.execute(
            "INSERT OR IGNORE INTO watch_hits (watchlist_id, raw_post_id, matched_terms, created_at) "
            "VALUES (?, ?, ?, ?)",
            (wl["id"], r["id"], json.dumps(matched), now),
        )
        new += cur.rowcount
    conn.execute("UPDATE watchlists SET last_post_id = ? WHERE id = ?", (max_id, wl["id"]))
    conn.commit()
    return new


def check_all(conn: sqlite3.Connection) -> int:
    return sum(check(conn, wl) for wl in conn.execute("SELECT * FROM watchlists").fetchall())


def create(conn: sqlite3.Connection, name: str, terms: list[Any]) -> dict:
    clean = _clean_terms(terms)
    if not clean:
        raise ValueError("Add at least one term of 2 or more characters.")
    cur = conn.execute(
        "INSERT INTO watchlists (name, terms_json, created_at) VALUES (?, ?, ?)",
        (name.strip() or clean[0], json.dumps(clean), time.time()),
    )
    conn.commit()
    wl = conn.execute("SELECT * FROM watchlists WHERE id = ?", (cur.lastrowid,)).fetchone()
    # Backfill against everything already collected so a new watchlist shows
    # past mentions immediately, but mark them seen: whoever creates a
    # watchlist wants the history, not 40 "new" alerts at once.
    check(conn, wl)
    conn.execute("UPDATE watch_hits SET seen = 1 WHERE watchlist_id = ?", (wl["id"],))
    conn.commit()
    return get(conn, wl["id"])


def get(conn: sqlite3.Connection, wid: int) -> dict:
    wl = conn.execute("SELECT * FROM watchlists WHERE id = ?", (wid,)).fetchone()
    if wl is None:
        raise KeyError(wid)
    counts = conn.execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(seen = 0), 0) AS unseen "
        "FROM watch_hits WHERE watchlist_id = ?",
        (wid,),
    ).fetchone()
    return {"id": wl["id"], "name": wl["name"], "terms": json.loads(wl["terms_json"]),
            "created_at": wl["created_at"], "hits": counts["total"], "unseen": counts["unseen"]}


def list_all(conn: sqlite3.Connection) -> list[dict]:
    return [get(conn, r["id"]) for r in conn.execute("SELECT id FROM watchlists ORDER BY created_at DESC")]


def delete(conn: sqlite3.Connection, wid: int) -> bool:
    cur = conn.execute("DELETE FROM watchlists WHERE id = ?", (wid,))
    conn.commit()
    return cur.rowcount > 0


def alerts(conn: sqlite3.Connection, watchlist_id: int | None = None, limit: int = 100) -> list[dict]:
    where, args = ("WHERE h.watchlist_id = ?", [watchlist_id]) if watchlist_id else ("", [])
    rows = conn.execute(
        f"""
        SELECT h.id, h.watchlist_id, w.name AS watchlist, h.raw_post_id, h.matched_terms,
               h.created_at, h.seen, rp.thread_title, rp.category, rp.source,
               rp.source_created_at, la.intent
        FROM watch_hits h
        JOIN watchlists w ON w.id = h.watchlist_id
        JOIN raw_posts rp ON rp.id = h.raw_post_id
        LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id
        {where}
        ORDER BY h.seen ASC, rp.source_created_at DESC
        LIMIT ?
        """,
        args + [limit],
    ).fetchall()
    return [{**dict(r), "matched_terms": json.loads(r["matched_terms"]), "seen": bool(r["seen"])}
            for r in rows]


def mark_seen(conn: sqlite3.Connection, ids: list[int] | None) -> int:
    if ids:
        q = ",".join("?" for _ in ids)
        cur = conn.execute(f"UPDATE watch_hits SET seen = 1 WHERE id IN ({q})", ids)
    else:
        cur = conn.execute("UPDATE watch_hits SET seen = 1 WHERE seen = 0")
    conn.commit()
    return cur.rowcount
