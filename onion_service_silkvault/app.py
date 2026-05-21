"""
SilkVault — synthetic darknet forum #2 (Flask app).

This is SentinelX's *second* synthetic .onion forum, built deliberately
*different* from DarkBay (onion_service/). Its job is to prove the scraper is
not hardcoded to one site: where DarkBay exposes a convenient JSON API,
SilkVault exposes **only HTML pages** — exactly like a real darknet forum. The
scraper must therefore parse markup to ingest SilkVault.

Structural differences from DarkBay, all on purpose:
    * Vocabulary: "listings" and "messages", not "threads" and "posts".
    * URL scheme: /board/<slug> and /listing/<id>, not /category/ and /thread/.
    * Markup: <div>-based cards, not <table> rows; different CSS class names.
    * No /api/* endpoints at all — there is nothing but HTML and /healthz.

Endpoints:
    GET  /                      Front page: recent listings + board nav.
    GET  /board/<slug>          Listings filtered by board.
    GET  /listing/<int:id>      One listing with all its messages.
    GET  /new                   HTML form to post a new listing.
    POST /new                   Create a listing from the form, then redirect.
    GET  /healthz               Liveness check (the only non-HTML route).

The /new form is a plain HTML POST (no JSON) — it keeps SilkVault's "HTML
only, no API" character intact while letting a demo post a fresh thread live
in Tor Browser, which the SentinelX scraper then picks up on its next crawl.

Database file path is read from VAULT_DB env var (default: /data/vault.db
inside the container; ./vault.db when running locally).
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from flask import (
    Flask, abort, g, jsonify, redirect, render_template, request, url_for,
)

# SilkVault's boards — kept in code so the /new form can offer them as a
# dropdown. Mirrors the board set used by seed_data.py.
BOARDS = [
    "exploits", "accounts", "network-access", "malware", "data-leaks", "lounge",
]

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

DB_PATH = os.environ.get(
    "VAULT_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "vault.db"),
)

app = Flask(__name__)


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #

def get_db() -> sqlite3.Connection:
    """One sqlite connection per request, attached to flask.g."""
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exc: BaseException | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


# --------------------------------------------------------------------------- #
# HTML routes — the ONLY way to read content from SilkVault.
# --------------------------------------------------------------------------- #

@app.route("/")
def index():
    db = get_db()
    listings = db.execute(
        """
        SELECT l.id, l.title, l.board, l.vendor, l.created_at,
               COUNT(m.id) AS message_count,
               MAX(m.created_at) AS last_message_at
        FROM listings l
        LEFT JOIN messages m ON m.listing_id = l.id
        GROUP BY l.id
        ORDER BY COALESCE(MAX(m.created_at), l.created_at) DESC
        LIMIT 60
        """
    ).fetchall()

    boards = db.execute(
        "SELECT board, COUNT(*) AS n FROM listings GROUP BY board ORDER BY n DESC"
    ).fetchall()

    return render_template("index.html", listings=listings, boards=boards)


@app.route("/board/<slug>")
def board(slug: str):
    db = get_db()
    listings = db.execute(
        """
        SELECT l.id, l.title, l.board, l.vendor, l.created_at,
               COUNT(m.id) AS message_count
        FROM listings l
        LEFT JOIN messages m ON m.listing_id = l.id
        WHERE l.board = ?
        GROUP BY l.id
        ORDER BY l.created_at DESC
        """,
        (slug,),
    ).fetchall()
    return render_template("board.html", board=slug, listings=listings)


@app.route("/listing/<int:listing_id>")
def listing(listing_id: int):
    db = get_db()
    lst = db.execute(
        "SELECT * FROM listings WHERE id = ?", (listing_id,)
    ).fetchone()
    if lst is None:
        abort(404)
    messages = db.execute(
        "SELECT * FROM messages WHERE listing_id = ? ORDER BY created_at ASC",
        (listing_id,),
    ).fetchall()
    return render_template("listing.html", listing=lst, messages=messages)


@app.route("/new", methods=["GET", "POST"])
def new_listing():
    """Post a new listing to SilkVault.

    GET renders an HTML form; POST saves the listing + its opening message and
    redirects to the new listing page. Deliberately a plain HTML form, not a
    JSON API — it lets a live demo post a fresh darknet thread in Tor Browser,
    which the SentinelX scraper then ingests on its next crawl.
    """
    if request.method == "GET":
        return render_template("new.html", boards=BOARDS, error=None)

    title = (request.form.get("title") or "").strip()
    board = (request.form.get("board") or "").strip()
    vendor = (request.form.get("vendor") or "").strip()
    body = (request.form.get("body") or "").strip()

    if not all((title, board, vendor, body)):
        return render_template(
            "new.html", boards=BOARDS,
            error="all fields are required.",
        ), 400
    if board not in BOARDS:
        board = "lounge"

    now = time.time()
    db = get_db()
    cur = db.execute(
        "INSERT INTO listings (title, board, vendor, created_at) "
        "VALUES (?, ?, ?, ?)",
        (title, board, vendor, now),
    )
    listing_id = cur.lastrowid
    db.execute(
        "INSERT INTO messages (listing_id, author, body, created_at) "
        "VALUES (?, ?, ?, ?)",
        (listing_id, vendor, body, now),
    )
    db.commit()
    return redirect(url_for("listing", listing_id=listing_id))


@app.route("/healthz")
def healthz():
    try:
        with closing(sqlite3.connect(DB_PATH)) as c:
            c.execute("SELECT 1")
        return jsonify({"ok": True, "db": DB_PATH})
    except Exception as e:  # noqa: BLE001 — liveness endpoint reports any failure
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


@app.template_filter("iso_ts")
def iso_ts(value: float | int | None) -> str:
    """ISO-8601 timestamp for the machine-readable <time datetime="..."> attr.

    The SilkVault HTML carries an explicit epoch in a data-epoch attribute too,
    so the scraper has a clean numeric value to parse without date math.
    """
    if value is None:
        return ""
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except Exception:
        return str(value)


# --------------------------------------------------------------------------- #
# Entrypoint (for local `python app.py`; in Docker we use gunicorn)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
