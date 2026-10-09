"""SentinelX REST API over the SQLite store.
Run: python -m uvicorn backend.api.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import asyncio
import time as _time
from collections import defaultdict, deque

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response, StreamingResponse

from backend.api import investigations as inv
from backend.llm.lenses import LENSES, list_lenses

# Config from env vars (same code runs locally and on Render)
DB_PATH = Path(
    os.environ.get(
        "SENTINELX_DB_PATH",
        str(Path(__file__).resolve().parents[1] / "db" / "sentinelx.db"),
    )
)
# Allowed frontend origins, comma-separated ("*" = allow all)
_cors_raw = os.environ.get("CORS_ORIGINS", "*").strip()
CORS_ORIGINS = (
    ["*"] if _cors_raw == "*" else [o.strip() for o in _cors_raw.split(",") if o.strip()]
)


# Open a DB connection; rows act like dicts (row["body"])
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL = reads and writes can happen at once; busy_timeout = wait instead of failing
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


class _PerThreadConn:
    """Acts like one DB connection, but each thread gets its own (avoids random 500s)."""

    def __init__(self) -> None:
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

    # Return this thread's connection, opening it the first time
    def _get(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._local.conn = _connect()
            with self._lock:
                self._all.append(conn)
        return conn

    # Any call like conn.execute(...) goes to this thread's own connection
    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)

    # Close every connection on shutdown
    def close(self) -> None:
        with self._lock:
            for c in self._all:
                c.close()
            self._all.clear()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup: make sure all DB tables exist, then set up per-thread connections
    from backend.db.store import Store

    Store(DB_PATH).close()
    app.state.conn = _PerThreadConn()
    try:
        yield
    finally:
        app.state.conn.close()


# The FastAPI app
app = FastAPI(title="SentinelX I", version="0.6.5", lifespan=lifespan)

# --------------------------------------------------------------------------- #
# Write protection: API key on writes + per-IP rate limit on expensive calls
# --------------------------------------------------------------------------- #

# API key (empty = no key needed), rate limit, and which endpoints count as expensive
API_KEY = os.environ.get("SENTINELX_API_KEY", "").strip()
RATE_LIMIT = int(os.environ.get("SENTINELX_RATE_LIMIT", "10"))   # max requests
RATE_WINDOW_S = 60.0                                                # per 60 seconds
_EXPENSIVE = ("/scrape-jobs", "/rerun", "/discover/search")
# Recent request times per IP address
_hits: dict[str, deque[float]] = defaultdict(deque)


# Runs before every request
@app.middleware("http")
async def protect_writes(request: Request, call_next):
    # Only writes are checked; reads are always allowed
    if request.method in ("POST", "PATCH", "DELETE"):
        # Wrong or missing API key -> 401
        if API_KEY and request.headers.get("x-api-key") != API_KEY:
            return JSONResponse({"detail": "missing or invalid X-API-Key"}, status_code=401)
        # Expensive endpoint: drop request times older than 60s, then check the count
        if any(request.url.path.endswith(p) for p in _EXPENSIVE):
            ip = request.client.host if request.client else "unknown"
            now = _time.monotonic()
            q = _hits[ip]
            while q and now - q[0] > RATE_WINDOW_S:
                q.popleft()
            # Too many requests -> 429
            if len(q) >= RATE_LIMIT:
                return JSONResponse(
                    {"detail": f"rate limit: {RATE_LIMIT} per {int(RATE_WINDOW_S)}s"},
                    status_code=429,
                    headers={"Retry-After": str(int(RATE_WINDOW_S - (now - q[0])) + 1)},
                )
            q.append(now)
    return await call_next(request)


# Let the React frontend call this API from another address
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# DB row -> plain dict (so FastAPI can send it as JSON)
def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


# Decode a JSON string from the DB; return the text as-is if it isn't JSON
def _maybe_json(s: str | None) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s


def _mitigations_for_techniques(
    c: sqlite3.Connection, technique_ids: list[str]
) -> list[dict[str, Any]]:
    """Look up MITRE defences for these techniques, ranked by how many they cover."""
    tids = [t for t in dict.fromkeys(technique_ids) if t]
    # No techniques -> no mitigations
    if not tids:
        return []
    placeholders = ",".join("?" for _ in tids)
    # Every mitigation linked to any of these techniques
    rows = c.execute(
        f"""
        SELECT m.mitigation_id, m.name, m.description, m.url, tm.technique_id
        FROM technique_mitigations tm
        JOIN mitre_mitigations m ON m.mitigation_id = tm.mitigation_id
        WHERE tm.technique_id IN ({placeholders})
        """,
        tids,
    ).fetchall()
    by_mid: dict[str, dict[str, Any]] = {}
    # Group by mitigation, collecting which techniques it covers
    for r in rows:
        mid = r["mitigation_id"]
        entry = by_mid.get(mid)
        if entry is None:
            entry = {
                "mitigation_id": mid,
                "name": r["name"],
                "description": r["description"],
                "url": r["url"],
                "addresses": [],
            }
            by_mid[mid] = entry
        entry["addresses"].append(r["technique_id"])
    out = list(by_mid.values())
    for e in out:
        e["addresses"] = sorted(set(e["addresses"]))
        e["coverage"] = len(e["addresses"])
    # Most broadly-applicable mitigation first, then stable by code.
    out.sort(key=lambda e: (-e["coverage"], e["mitigation_id"]))
    return out


# --------------------------------------------------------------------------- #
# Health + meta
# --------------------------------------------------------------------------- #

# Is the API alive?
@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


# Numbers for the dashboard (counts, breakdowns, top techniques)
@app.get("/stats")
def stats() -> dict[str, Any]:
    c = app.state.conn
    # Small helpers: one() = a single number, rows() = a list of dicts
    one = lambda sql, *a: c.execute(sql, a).fetchone()[0]
    rows = lambda sql, *a: [_row_to_dict(r) for r in c.execute(sql, a).fetchall()]
    return {
        "posts_total": one("SELECT COUNT(*) FROM raw_posts"),
        "posts_extracted": one("SELECT COUNT(*) FROM raw_posts WHERE processed_at IS NOT NULL"),
        "posts_llm_analysed": one("SELECT COUNT(*) FROM llm_analyses"),
        "posts_mitre_matched": one("SELECT COUNT(*) FROM post_processing_state WHERE stage='mitre'"),
        "iocs_total": one("SELECT COUNT(*) FROM iocs"),
        "entities_total": one("SELECT COUNT(*) FROM entities"),
        "techniques_corpus": one("SELECT COUNT(*) FROM mitre_techniques"),
        "post_techniques_total": one("SELECT COUNT(*) FROM post_techniques"),
        "by_category": rows(
            "SELECT category, COUNT(*) AS n FROM raw_posts GROUP BY category ORDER BY n DESC"
        ),
        "by_intent": rows(
            "SELECT intent, COUNT(*) AS n FROM llm_analyses "
            "WHERE intent IS NOT NULL GROUP BY intent ORDER BY n DESC"
        ),
        "by_ioc_type": rows(
            "SELECT ioc_type, COUNT(*) AS n FROM iocs GROUP BY ioc_type ORDER BY n DESC"
        ),
        "by_technique_source": rows(
            "SELECT source, COUNT(*) AS n FROM post_techniques GROUP BY source"
        ),
        # Posts per language (not detected yet = 'unknown')
        "by_language": rows(
            "SELECT COALESCE(lang, 'unknown') AS lang, COUNT(*) AS n "
            "FROM raw_posts GROUP BY COALESCE(lang, 'unknown') ORDER BY n DESC"
        ),
        "posts_translated": one(
            "SELECT COUNT(*) FROM raw_posts WHERE body_en IS NOT NULL"
        ),
        "top_techniques": rows(
            # Top techniques, split by how they were found
            "SELECT pt.technique_id, mt.name, COUNT(*) AS n, "
            "  SUM(pt.source = 'llm_verified') AS verified, "
            "  SUM(pt.source = 'semantic') AS semantic, "
            "  SUM(pt.source = 'llm_unverified') AS unverified "
            "FROM post_techniques pt "
            "LEFT JOIN mitre_techniques mt ON mt.technique_id = pt.technique_id "
            "GROUP BY pt.technique_id ORDER BY n DESC LIMIT 15"
        ),
    }


# --------------------------------------------------------------------------- #
# Posts
# --------------------------------------------------------------------------- #

# List posts, with optional filters and paging
@app.get("/posts")
def list_posts(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    category: str | None = None,
    intent: str | None = None,
    technique: str | None = Query(None, description="Filter to posts mapped to this T-code"),
    q: str | None = Query(None, description="Substring search over body / thread_title"),
) -> dict[str, Any]:
    c = app.state.conn
    where: list[str] = []
    args: list[Any] = []
    join_pt = ""
    # Add a WHERE condition for each filter that was given
    if category:
        where.append("rp.category = ?"); args.append(category)
    if intent:
        where.append("la.intent = ?"); args.append(intent)
    if technique:
        join_pt = "JOIN post_techniques pt ON pt.raw_post_id = rp.id AND pt.technique_id = ?"
        args.insert(0, technique)
    if q:
        # Search original text, English translation and title
        where.append(
            "(rp.body LIKE ? OR rp.body_en LIKE ? OR rp.thread_title LIKE ?)"
        )
        like = f"%{q}%"; args.extend([like, like, like])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    # Total number of matching posts (for paging)
    count_sql = (
        f"SELECT COUNT(DISTINCT rp.id) AS n FROM raw_posts rp "
        f"LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id {join_pt} {where_sql}"
    )
    total = c.execute(count_sql, args).fetchone()["n"]

    # Preview shows the English translation if there is one
    list_sql = (
        f"SELECT DISTINCT rp.id, rp.thread_title, rp.category, rp.author, "
        f"  substr(COALESCE(rp.body_en, rp.body), 1, 280) AS body_preview, "
        f"  rp.source_created_at, rp.lang, rp.lang_confidence, "
        f"  la.intent, la.summary "
        f"FROM raw_posts rp LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id {join_pt} "
        f"{where_sql} ORDER BY rp.source_created_at DESC LIMIT ? OFFSET ?"
    )
    # The current page of posts
    items = [_row_to_dict(r) for r in c.execute(list_sql, args + [limit, offset])]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


# Everything about one post: text, LLM analysis, IOCs, entities, techniques, mitigations
@app.get("/posts/{post_id}")
def get_post(post_id: int) -> dict[str, Any]:
    c = app.state.conn
    row = c.execute("SELECT * FROM raw_posts WHERE id = ?", (post_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"post {post_id} not found")
    post = _row_to_dict(row)

    # LLM analysis (JSON columns decoded, raw replies left out)
    la = c.execute("SELECT * FROM llm_analyses WHERE raw_post_id = ?", (post_id,)).fetchone()
    analysis: dict[str, Any] | None = None
    if la:
        analysis = _row_to_dict(la)
        analysis["targets"] = _maybe_json(analysis.pop("targets_json"))
        analysis["techniques"] = _maybe_json(analysis.pop("techniques_json"))
        analysis.pop("raw_responses", None)

    # IOCs and entities found in the post
    iocs = [_row_to_dict(r) for r in c.execute(
        "SELECT ioc_type, value, span_start, span_end FROM iocs WHERE raw_post_id = ? "
        "ORDER BY ioc_type, value", (post_id,))]
    entities = [_row_to_dict(r) for r in c.execute(
        "SELECT label, text, span_start, span_end FROM entities WHERE raw_post_id = ? "
        "ORDER BY label, text", (post_id,))]
    # Matched techniques, best evidence first (verified, semantic, unverified)
    techniques = [_row_to_dict(r) for r in c.execute(
        "SELECT pt.technique_id, pt.source, pt.score, pt.evidence, "
        "       mt.name, mt.tactics, mt.url, mt.is_subtechnique, mt.parent_id "
        "FROM post_techniques pt LEFT JOIN mitre_techniques mt "
        "  ON mt.technique_id = pt.technique_id "
        "WHERE pt.raw_post_id = ? "
        "ORDER BY CASE pt.source WHEN 'llm_verified' THEN 0 WHEN 'semantic' THEN 1 ELSE 2 END, "
        "         pt.score DESC NULLS LAST, pt.technique_id", (post_id,))]
    # "initial-access,execution" -> ["initial-access", "execution"]
    for t in techniques:
        if t.get("tactics"):
            t["tactics"] = [x for x in t["tactics"].split(",") if x]

    # Defences for those techniques
    mitigations = _mitigations_for_techniques(
        c, [t["technique_id"] for t in techniques]
    )

    return {"post": post, "analysis": analysis, "iocs": iocs,
            "entities": entities, "techniques": techniques,
            "mitigations": mitigations}


# --------------------------------------------------------------------------- #
# MITRE
# --------------------------------------------------------------------------- #

# List MITRE techniques with how many posts mention each
@app.get("/techniques")
def list_techniques(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    q: str | None = None,
    only_seen: bool = Query(False, description="Only techniques attached to >=1 post"),
) -> dict[str, Any]:
    c = app.state.conn
    where: list[str] = []
    args: list[Any] = []
    if q:
        where.append("(mt.technique_id LIKE ? OR mt.name LIKE ?)")
        like = f"%{q}%"; args.extend([like, like])
    if only_seen:
        where.append("EXISTS (SELECT 1 FROM post_techniques pt WHERE pt.technique_id = mt.technique_id)")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = c.execute(
        f"SELECT COUNT(*) AS n FROM mitre_techniques mt {where_sql}", args
    ).fetchone()["n"]
    rows = c.execute(
        f"SELECT mt.technique_id, mt.name, mt.tactics, mt.url, mt.is_subtechnique, mt.parent_id, "
        f"  (SELECT COUNT(*) FROM post_techniques pt WHERE pt.technique_id = mt.technique_id) AS post_count "
        f"FROM mitre_techniques mt {where_sql} "
        f"ORDER BY post_count DESC, mt.technique_id LIMIT ? OFFSET ?",
        args + [limit, offset]
    ).fetchall()
    items = [_row_to_dict(r) for r in rows]
    for it in items:
        if it.get("tactics"):
            it["tactics"] = [x for x in it["tactics"].split(",") if x]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


# One technique plus every post mapped to it
@app.get("/techniques/{technique_id}")
def get_technique(technique_id: int | str) -> dict[str, Any]:
    tid = str(technique_id).upper()
    c = app.state.conn
    row = c.execute(
        "SELECT technique_id, name, description, tactics, url, is_subtechnique, parent_id "
        "FROM mitre_techniques WHERE technique_id = ?", (tid,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"technique {tid} not found")
    out = _row_to_dict(row)
    if out.get("tactics"):
        out["tactics"] = [x for x in out["tactics"].split(",") if x]
    out["posts"] = [_row_to_dict(r) for r in c.execute(
        "SELECT pt.raw_post_id, pt.source, pt.score, rp.thread_title, rp.category "
        "FROM post_techniques pt JOIN raw_posts rp ON rp.id = pt.raw_post_id "
        "WHERE pt.technique_id = ? ORDER BY pt.score DESC NULLS LAST, pt.raw_post_id",
        (tid,))]
    return out


# --------------------------------------------------------------------------- #
# IOCs / Entities (cross-post lookups)
# --------------------------------------------------------------------------- #

# IOCs grouped by value, with how many posts contain each
@app.get("/iocs")
def list_iocs(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    ioc_type: str | None = None,
    value: str | None = None,
) -> dict[str, Any]:
    c = app.state.conn
    where: list[str] = []
    args: list[Any] = []
    if ioc_type:
        where.append("ioc_type = ?"); args.append(ioc_type)
    if value:
        where.append("value LIKE ?"); args.append(f"%{value}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    total = c.execute(f"SELECT COUNT(*) AS n FROM iocs {where_sql}", args).fetchone()["n"]
    items = [_row_to_dict(r) for r in c.execute(
        f"SELECT ioc_type, value, COUNT(*) AS occurrences, "
        f"       GROUP_CONCAT(raw_post_id) AS post_ids "
        f"FROM iocs {where_sql} GROUP BY ioc_type, value "
        f"ORDER BY occurrences DESC, ioc_type, value LIMIT ? OFFSET ?",
        args + [limit, offset])]
    # "1,5,9" -> [1, 5, 9]
    for it in items:
        it["post_ids"] = [int(x) for x in (it["post_ids"] or "").split(",") if x]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


# --------------------------------------------------------------------------- #
# Investigations + lenses
# --------------------------------------------------------------------------- #

# Available lenses
@app.get("/lenses")
def get_lenses() -> dict[str, Any]:
    return {"items": list_lenses()}


# All investigations
@app.get("/investigations")
def list_investigations() -> dict[str, Any]:
    return {"items": inv.list_investigations(app.state.conn)}


# Create an investigation (name required; lens must exist)
@app.post("/investigations", status_code=201)
def create_investigation(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    lens = payload.get("lens")
    if lens is not None and lens not in LENSES:
        raise HTTPException(400, f"unknown lens '{lens}'. choices: {sorted(LENSES)}")
    return inv.create_investigation(
        app.state.conn,
        name=name,
        description=payload.get("description"),
        filters=payload.get("filters") or {},
        lens=lens,
    )


# One investigation with its matching posts and mitigations
@app.get("/investigations/{investigation_id}")
def get_investigation(investigation_id: int) -> dict[str, Any]:
    try:
        return inv.get_investigation(app.state.conn, investigation_id)
    except KeyError:
        raise HTTPException(404, f"investigation {investigation_id} not found")


# Change an investigation's name, description, filters or lens
@app.patch("/investigations/{investigation_id}")
def update_investigation(
    investigation_id: int, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    lens = payload.get("lens")
    if lens is not None and lens not in LENSES:
        raise HTTPException(400, f"unknown lens '{lens}'. choices: {sorted(LENSES)}")
    try:
        return inv.update_investigation(
            app.state.conn,
            investigation_id,
            name=payload.get("name"),
            description=payload.get("description"),
            filters=payload.get("filters"),
            lens=lens,
        )
    except KeyError:
        raise HTTPException(404, f"investigation {investigation_id} not found")


# Delete an investigation
@app.delete("/investigations/{investigation_id}")
def delete_investigation(investigation_id: int) -> Response:
    if not inv.delete_investigation(app.state.conn, investigation_id):
        raise HTTPException(404, f"investigation {investigation_id} not found")
    return Response(status_code=204)


# Download an investigation as a PDF
@app.get("/investigations/{investigation_id}/export")
def export_investigation_pdf(investigation_id: int) -> Response:
    from backend.api.export import render_investigation_pdf
    try:
        pdf_bytes, filename = render_investigation_pdf(app.state.conn, investigation_id)
    except KeyError:
        raise HTTPException(404, f"investigation {investigation_id} not found")
    except OSError as e:
        # WeasyPrint raises OSError when GTK runtime DLLs are missing on PATH.
        raise HTTPException(
            500,
            f"PDF rendering failed (likely GTK runtime missing on PATH): {e}",
        )
    except Exception as e:
        raise HTTPException(500, f"PDF rendering failed: {e}")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Re-run the lens summary with the LLM
@app.post("/investigations/{investigation_id}/rerun")
def rerun_investigation(
    investigation_id: int, payload: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    model = (payload or {}).get("model", "mistral")
    try:
        return inv.run_lens_summary(app.state.conn, investigation_id, model=model)
    except KeyError:
        raise HTTPException(404, f"investigation {investigation_id} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"lens rerun failed: {e}")


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #

@app.get("/healthz/full")
def healthz_full() -> dict[str, Any]:
    """Check DB, Tor, Ollama and pipeline backlog."""
    import socket
    import time as _time

    # Each check below fills in out["checks"]; a failure marks that check "down"
    c = app.state.conn
    out: dict[str, Any] = {"status": "ok", "checks": {}}

    # DB check
    t0 = _time.perf_counter()
    try:
        c.execute("SELECT 1").fetchone()
        out["checks"]["db"] = {
            "status": "up",
            "latency_ms": int((_time.perf_counter() - t0) * 1000),
            "path": str(DB_PATH),
        }
    except Exception as e:
        out["checks"]["db"] = {"status": "down", "error": str(e)}
        out["status"] = "degraded"

    # Tor check (only tests the port is open)
    t0 = _time.perf_counter()
    try:
        with socket.create_connection(("127.0.0.1", 9050), timeout=2.0):
            out["checks"]["tor_socks"] = {
                "status": "up",
                "latency_ms": int((_time.perf_counter() - t0) * 1000),
            }
    except Exception as e:
        out["checks"]["tor_socks"] = {"status": "down", "error": str(e)}

    # Ollama (local LLM) check
    t0 = _time.perf_counter()
    try:
        from backend.llm.client import OllamaClient
        with OllamaClient() as cli:
            models = cli.health()
        out["checks"]["ollama"] = {
            "status": "up",
            "latency_ms": int((_time.perf_counter() - t0) * 1000),
            "models": models,
        }
    except Exception as e:
        out["checks"]["ollama"] = {"status": "down", "error": str(e)}

    # Posts still waiting at each pipeline stage
    try:
        unprocessed = c.execute(
            "SELECT COUNT(*) FROM raw_posts WHERE processed_at IS NULL"
        ).fetchone()[0]
        pending_llm = c.execute(
            "SELECT COUNT(*) FROM raw_posts rp WHERE rp.processed_at IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM post_processing_state pps "
            "  WHERE pps.raw_post_id = rp.id AND pps.stage = 'llm')"
        ).fetchone()[0]
        pending_mitre = c.execute(
            "SELECT COUNT(*) FROM post_processing_state pps_llm "
            "WHERE pps_llm.stage = 'llm' AND NOT EXISTS ("
            "  SELECT 1 FROM post_processing_state pps_m "
            "  WHERE pps_m.raw_post_id = pps_llm.raw_post_id AND pps_m.stage='mitre')"
        ).fetchone()[0]
        out["checks"]["pipeline"] = {
            "status": "up",
            "pending_extraction": unprocessed,
            "pending_llm": pending_llm,
            "pending_mitre": pending_mitre,
        }
    except Exception as e:
        out["checks"]["pipeline"] = {"status": "down", "error": str(e)}

    return out


# --------------------------------------------------------------------------- #
# Scrape jobs: run the full pipeline on a .onion site
# --------------------------------------------------------------------------- #

@app.post("/scrape-jobs", status_code=202)
def create_scrape_job(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Start scrape -> extract -> LLM -> MITRE in the background; poll /scrape-jobs/{id}."""
    import threading
    from urllib.parse import urlparse

    from backend.jobs import runner

    # Mode 1: specific pages picked on the Discover page
    # The two modes: a list of pages, or one forum address
    urls = [str(u).strip() for u in (payload.get("urls") or []) if str(u).strip()]
    if urls:
        # Every URL must be a .onion address, max 25 per job
        bad = [u for u in urls if not urlparse(u if "://" in u else f"http://{u}").netloc.endswith(".onion")]
        if bad:
            raise HTTPException(400, f"every url must be a .onion page (got {bad[0]!r})")
        if len(urls) > 25:
            raise HTTPException(400, "send at most 25 pages per job")
        c = app.state.conn
        job_id = runner.create_job(c, onion_url=f"{len(urls)} discovered pages", source="discovery")
        threading.Thread(
            target=runner.run_job,
            kwargs={"job_id": job_id, "onion_url": urls[0], "source": "discovery",
                    "skip_llm": bool(payload.get("skip_llm")), "urls": urls,
                    "proxy": "socks5://127.0.0.1:9050"},
            daemon=True, name=f"scrape-job-{job_id}",
        ).start()
        return runner.get_job(c, job_id) or {"id": job_id, "status": "queued"}

    raw = str(payload.get("onion_url") or "").strip()
    if not raw:
        raise HTTPException(400, "onion_url or urls is required")
    # Mode 2: a whole forum by .onion address
    host = urlparse(raw if "://" in raw else f"http://{raw}").netloc or raw
    if not host.endswith(".onion"):
        raise HTTPException(
            400, f"onion_url must be a .onion address (got {host!r})"
        )
    source = str(payload.get("source") or "").strip() or host
    skip_llm = bool(payload.get("skip_llm"))

    # Create the job row (status "queued")
    c = app.state.conn
    job_id = runner.create_job(c, onion_url=raw, source=source)

    # Run in a background thread (the job opens its own DB connection)
    t = threading.Thread(
        target=runner.run_job,
        kwargs={"job_id": job_id, "onion_url": raw, "source": source,
                "skip_llm": skip_llm},
        daemon=True,
        name=f"scrape-job-{job_id}",
    )
    t.start()

    # Return the new job so the UI can start polling it
    job = runner.get_job(c, job_id)
    return job or {"id": job_id, "status": "queued"}


# --------------------------------------------------------------------------- #
# Discovery: search dark-web engines before scraping
# --------------------------------------------------------------------------- #

# Which search engines the Discover page can use
@app.get("/discover/engines")
def discover_engines() -> dict[str, Any]:
    from backend.discovery.search import available_engines
    return {"items": available_engines()}


@app.post("/discover/search")
def discover_search(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Search local DB and/or dark-web engines; each engine's errors go in `errors`."""
    from backend.discovery.search import run_search

    # Check the query, then run the search
    query = str(payload.get("query") or "").strip()
    if len(query) < 2:
        raise HTTPException(400, "query must be at least 2 characters")
    engines = payload.get("engines") or ["local"]
    return run_search(app.state.conn, query[:300], engines=engines, refine=bool(payload.get("refine")))


# --------------------------------------------------------------------------- #
# Watchlists + alerts
# --------------------------------------------------------------------------- #

# All watchlists (checks for new matches first)
@app.get("/watchlists")
def get_watchlists() -> dict[str, Any]:
    from backend.api import watch
    watch.check_all(app.state.conn)
    return {"items": watch.list_all(app.state.conn)}


# Create a watchlist; terms can be a list or "a, b, c"
@app.post("/watchlists", status_code=201)
def create_watchlist(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from backend.api import watch
    terms = payload.get("terms") or []
    if isinstance(terms, str):
        terms = terms.split(",")
    try:
        return watch.create(app.state.conn, str(payload.get("name") or ""), terms)
    except ValueError as e:
        raise HTTPException(400, str(e))


# Delete a watchlist
@app.delete("/watchlists/{watchlist_id}")
def delete_watchlist(watchlist_id: int) -> Response:
    from backend.api import watch
    if not watch.delete(app.state.conn, watchlist_id):
        raise HTTPException(404, f"watchlist {watchlist_id} not found")
    return Response(status_code=204)


# Alerts list + how many are unseen (checks for new matches first)
@app.get("/alerts")
def get_alerts(
    watchlist_id: int | None = None, limit: int = Query(100, ge=1, le=500)
) -> dict[str, Any]:
    from backend.api import watch
    watch.check_all(app.state.conn)
    items = watch.alerts(app.state.conn, watchlist_id, limit)
    unseen = app.state.conn.execute("SELECT COUNT(*) FROM watch_hits WHERE seen = 0").fetchone()[0]
    return {"items": items, "unseen": unseen}


# Mark alerts as seen (all of them if no ids are given)
@app.post("/alerts/seen")
def mark_alerts_seen(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    from backend.api import watch
    ids = [int(i) for i in (payload or {}).get("ids") or []]
    return {"updated": watch.mark_seen(app.state.conn, ids or None)}


# Recent scrape jobs
@app.get("/scrape-jobs")
def list_scrape_jobs(limit: int = Query(25, ge=1, le=100)) -> dict[str, Any]:
    from backend.jobs import runner
    return {"items": runner.list_jobs(app.state.conn, limit=limit)}


# One scrape job (the UI polls this for progress)
@app.get("/scrape-jobs/{job_id}")
def get_scrape_job(job_id: int) -> dict[str, Any]:
    from backend.jobs import runner
    job = runner.get_job(app.state.conn, job_id)
    if job is None:
        raise HTTPException(404, f"scrape job {job_id} not found")
    return job


# --------------------------------------------------------------------------- #
# Server-sent events (live timeline feed)
# --------------------------------------------------------------------------- #

# Small post summary sent to the live timeline
def _post_event(c: sqlite3.Connection, post_id: int) -> dict[str, Any]:
    # The post plus its LLM intent and summary
    row = c.execute(
        "SELECT rp.id, rp.thread_title, rp.category, rp.author, "
        "  substr(COALESCE(rp.body_en, rp.body), 1, 240) AS body_preview, "
        "  rp.source_created_at, rp.lang, rp.lang_confidence, "
        "  la.intent, la.summary "
        "FROM raw_posts rp LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id "
        "WHERE rp.id = ?",
        (post_id,),
    ).fetchone()
    if not row:
        return {"id": post_id, "missing": True}
    out = _row_to_dict(row)
    techs = c.execute(
        "SELECT pt.technique_id, pt.source, mt.name FROM post_techniques pt "
        "LEFT JOIN mitre_techniques mt ON mt.technique_id = pt.technique_id "
        "WHERE pt.raw_post_id = ? "
        "ORDER BY CASE pt.source WHEN 'llm_verified' THEN 0 "
        "  WHEN 'semantic' THEN 1 ELSE 2 END",
        (post_id,),
    ).fetchall()
    # Its techniques, best evidence first
    out["techniques"] = [
        {"technique_id": r["technique_id"], "source": r["source"], "name": r["name"]}
        for r in techs
    ]
    # Up to 8 of its IOCs
    iocs = c.execute(
        "SELECT ioc_type, value FROM iocs WHERE raw_post_id = ? "
        "ORDER BY ioc_type LIMIT 8",
        (post_id,),
    ).fetchall()
    out["iocs"] = [{"ioc_type": r["ioc_type"], "value": r["value"]} for r in iocs]
    return out


@app.get("/events")
async def events_stream(request: Request, since_id: int = Query(0, ge=0)):
    """Live stream of new posts (checks DB every 2s). Events: hello, post, ping."""

    # On reconnect the browser sends the last id it saw, so resume after it
    header_id = request.headers.get("last-event-id", "")
    start_after = int(header_id) if header_id.isdigit() else since_id

    # Newest post id in the DB
    def _latest_id() -> int:
        c = app.state.conn
        return c.execute("SELECT COALESCE(MAX(id), 0) FROM raw_posts").fetchone()[0]

    # Up to 50 posts newer than `after`
    def _new_posts(after: int) -> list[dict[str, Any]]:
        c = app.state.conn
        rows = c.execute(
            "SELECT id FROM raw_posts WHERE id > ? ORDER BY id ASC LIMIT 50",
            (after,),
        ).fetchall()
        return [_post_event(c, r["id"]) for r in rows]

    # The stream: hello first, then new posts as they arrive, with a ping every 15s
    async def gen():
        # DB calls run in a threadpool so they don't block other requests
        last = start_after
        latest = await run_in_threadpool(_latest_id)
        yield f"event: hello\ndata: {json.dumps({'latest_id': latest})}\n\n"

        last_ping = asyncio.get_event_loop().time()
        while True:
            if await request.is_disconnected():
                break

            batch = await run_in_threadpool(_new_posts, last)
            for payload in batch:
                yield f"id: {payload['id']}\nevent: post\ndata: {json.dumps(payload)}\n\n"
                last = payload["id"]
            # Full batch = still catching up, so skip the wait
            if len(batch) == 50:
                continue

            now = asyncio.get_event_loop().time()
            if now - last_ping > 15:
                yield f"event: ping\ndata: {json.dumps({'t': now})}\n\n"
                last_ping = now

            await asyncio.sleep(2.0)

    # Send it as a Server-Sent Events stream (no caching or buffering)
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",

        },
    )


# Entities grouped by text, with how many posts contain each
@app.get("/entities")
def list_entities(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    label: str | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    c = app.state.conn
    where: list[str] = []
    args: list[Any] = []
    if label:
        where.append("label = ?"); args.append(label)
    if text:
        where.append("text LIKE ?"); args.append(f"%{text}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    total = c.execute(f"SELECT COUNT(*) AS n FROM entities {where_sql}", args).fetchone()["n"]
    items = [_row_to_dict(r) for r in c.execute(
        f"SELECT label, text, COUNT(*) AS occurrences, "
        f"       GROUP_CONCAT(raw_post_id) AS post_ids "
        f"FROM entities {where_sql} GROUP BY label, text "
        f"ORDER BY occurrences DESC, label, text LIMIT ? OFFSET ?",
        args + [limit, offset])]
    for it in items:
        it["post_ids"] = [int(x) for x in (it["post_ids"] or "").split(",") if x]
    return {"total": total, "limit": limit, "offset": offset, "items": items}
