"""
Synthetic darknet forum — Flask app.

Serves a phpBB-style forum out of a SQLite database. Designed to run as a Tor
hidden service so the SentinelX scraper exercises the same SOCKS5/.onion plumbing
it would use against real darknet sites — without the legal/ethical/availability
problems of scraping the real ones.

Endpoints:
    GET /                       Front page: list of recent threads + category nav.
    GET /category/<slug>        Threads filtered by category.
    GET /thread/<int:thread_id> Single thread with all posts.
    GET /api/posts              JSON list of posts. Supports ?since=<unix_ts>
                                for incremental polling by the scraper.
    GET /api/post/<int:post_id> Single post as JSON.
    GET /healthz                Liveness check.

Database file path is read from FORUM_DB env var (default: /data/forum.db inside
the container; ./forum.db when running locally).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from flask import Flask, abort, g, jsonify, render_template, request

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

DB_PATH = os.environ.get(
    "FORUM_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "forum.db"),
)

app = Flask(__name__)


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #

def get_db() -> sqlite3.Connection:
    """One sqlite connection per request, attached to flask.g.

    Using Row factory so we get dict-like access to columns.
    """
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        # Foreign keys are off by default in SQLite for backwards compat.
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exc: BaseException | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


# --------------------------------------------------------------------------- #
# HTML routes
# --------------------------------------------------------------------------- #

@app.route("/")
def index():
    db = get_db()
    threads = db.execute(
        """
        SELECT t.id, t.title, t.category, t.author, t.created_at,
               COUNT(p.id) AS post_count,
               MAX(p.created_at) AS last_post_at
        FROM threads t
        LEFT JOIN posts p ON p.thread_id = t.id
        GROUP BY t.id
        ORDER BY COALESCE(MAX(p.created_at), t.created_at) DESC
        LIMIT 50
        """
    ).fetchall()

    categories = db.execute(
        "SELECT category, COUNT(*) AS n FROM threads GROUP BY category ORDER BY n DESC"
    ).fetchall()

    return render_template("index.html", threads=threads, categories=categories)


@app.route("/category/<slug>")
def category(slug: str):
    db = get_db()
    threads = db.execute(
        """
        SELECT t.id, t.title, t.category, t.author, t.created_at,
               COUNT(p.id) AS post_count
        FROM threads t
        LEFT JOIN posts p ON p.thread_id = t.id
        WHERE t.category = ?
        GROUP BY t.id
        ORDER BY t.created_at DESC
        """,
        (slug,),
    ).fetchall()
    if not threads:
        # Still render a page (empty category is valid) but flag it.
        pass
    return render_template("category.html", category=slug, threads=threads)


@app.route("/thread/<int:thread_id>")
def thread(thread_id: int):
    db = get_db()
    t = db.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
    if t is None:
        abort(404)
    posts = db.execute(
        "SELECT * FROM posts WHERE thread_id = ? ORDER BY created_at ASC",
        (thread_id,),
    ).fetchall()
    return render_template("thread.html", thread=t, posts=posts)


# --------------------------------------------------------------------------- #
# JSON API (what the scraper uses by default)
# --------------------------------------------------------------------------- #

@app.route("/api/posts")
def api_posts():
    """List posts. Optional filters:

    since=<unix_ts>     Only posts created strictly after this timestamp.
    limit=<int>         Max rows (default 200, capped at 1000).
    category=<slug>     Filter by thread category.
    """
    db = get_db()

    try:
        since = float(request.args.get("since", 0))
    except ValueError:
        since = 0.0
    try:
        limit = min(int(request.args.get("limit", 200)), 1000)
    except ValueError:
        limit = 200
    category = request.args.get("category")

    sql = [
        "SELECT p.id, p.thread_id, p.author, p.body, p.created_at,",
        "       t.title AS thread_title, t.category",
        "FROM posts p JOIN threads t ON t.id = p.thread_id",
        "WHERE p.created_at > ?",
    ]
    params: list[Any] = [since]
    if category:
        sql.append("AND t.category = ?")
        params.append(category)
    sql.append("ORDER BY p.created_at ASC LIMIT ?")
    params.append(limit)

    rows = db.execute(" ".join(sql), params).fetchall()
    return jsonify(
        {
            "count": len(rows),
            "since": since,
            "now": datetime.now(timezone.utc).timestamp(),
            "posts": [row_to_dict(r) for r in rows],
        }
    )


@app.route("/api/post/<int:post_id>")
def api_post(post_id: int):
    db = get_db()
    row = db.execute(
        """
        SELECT p.*, t.title AS thread_title, t.category
        FROM posts p JOIN threads t ON t.id = p.thread_id
        WHERE p.id = ?
        """,
        (post_id,),
    ).fetchone()
    if row is None:
        abort(404)
    return jsonify(row_to_dict(row))


@app.route("/healthz")
def healthz():
    try:
        with closing(sqlite3.connect(DB_PATH)) as c:
            c.execute("SELECT 1")
        return jsonify({"ok": True, "db": DB_PATH})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# --------------------------------------------------------------------------- #
# Template filters
# --------------------------------------------------------------------------- #

@app.template_filter("fmt_ts")
def fmt_ts(value: float | int | None) -> str:
    if value is None:
        return ""
    try:
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(value)


# --------------------------------------------------------------------------- #
# Entrypoint (for local `python app.py`; in Docker we use gunicorn)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # Bind to 0.0.0.0 so Tor (running as a sidecar container) can reach us.
    app.run(host="0.0.0.0", port=5000, debug=False)
