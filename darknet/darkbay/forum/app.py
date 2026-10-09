"""DarkBay: fake darknet forum (Flask) served over Tor for testing the scraper.
Has HTML pages plus a JSON API (/api/posts?since=...) the scraper uses.
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

# Database file (FORUM_DB env var, or forum.db next to this file)
DB_PATH = os.environ.get(
    "FORUM_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "forum.db"),
)

app = Flask(__name__)


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #

def get_db() -> sqlite3.Connection:
    """One DB connection per request."""
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        # Foreign keys are off by default in SQLite for backwards compat.
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


# Close the DB connection when the request ends
@app.teardown_appcontext
def close_db(exc: BaseException | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


# DB row -> plain dict (for JSON)
def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


# --------------------------------------------------------------------------- #
# HTML routes
# --------------------------------------------------------------------------- #

# Front page: latest 50 threads (most recently active first) + category list
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


# Threads in one category
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


# One thread with all its posts, oldest first
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

# The scraper's main endpoint: posts newer than ?since=
@app.route("/api/posts")
def api_posts():
    """List posts; filters: since, limit (max 1000), category."""
    db = get_db()

    # Read the query params (bad values fall back to defaults)
    try:
        since = float(request.args.get("since", 0))
    except ValueError:
        since = 0.0
    try:
        limit = min(int(request.args.get("limit", 200)), 1000)
    except ValueError:
        limit = 200
    category = request.args.get("category")

    # Build the SQL, adding the category filter only if given
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


# POST /api/threads
@app.route("/api/threads", methods=["POST"])
def api_create_thread():
    """Create a thread + first post (used to add fresh data during demos)."""
    # Check all fields are present
    data = request.get_json(silent=True) or {}
    required = ("title", "category", "author", "body")
    missing = [k for k in required if not data.get(k)]
    if missing:
        return jsonify({"error": f"missing: {', '.join(missing)}"}), 400

    # Save the thread, then its first post
    now = datetime.now(timezone.utc).timestamp()
    db = get_db()
    cur = db.execute(
        "INSERT INTO threads (title, category, author, created_at) "
        "VALUES (?, ?, ?, ?)",
        (data["title"], data["category"], data["author"], now),
    )
    thread_id = cur.lastrowid
    cur = db.execute(
        "INSERT INTO posts (thread_id, author, body, created_at) "
        "VALUES (?, ?, ?, ?)",
        (thread_id, data["author"], data["body"], now),
    )
    post_id = cur.lastrowid
    db.commit()
    return jsonify(
        {
            "thread_id": thread_id,
            "post_id": post_id,
            "created_at": now,
        }
    ), 201


# One post as JSON
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


# Liveness check: can we open the DB?
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

# Unix time -> "2026-01-31 14:05 UTC" in templates
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
