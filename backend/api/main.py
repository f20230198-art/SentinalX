"""FastAPI backend for SentinelX I.

Read-only HTTP layer over the SQLite store. Joins enrichment from Stages 2-5
into per-post views and exposes aggregate stats for the dashboard.

A single sqlite3 connection per process (with check_same_thread=False) is
sufficient: SQLite serialises reads internally and the pipeline workers are
the only writers — they hold their own connections.

Run:
    backend/.venv/Scripts/python.exe -m uvicorn backend.api.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import asyncio

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from backend.api import investigations as inv
from backend.llm.lenses import LENSES, list_lenses

# Env-driven config so the same code runs on a laptop and on Render.
DB_PATH = Path(
    os.environ.get(
        "SENTINELX_DB_PATH",
        str(Path(__file__).resolve().parents[1] / "db" / "sentinelx.db"),
    )
)
# Comma-separated allow-list. Default is "*" so local dev keeps working; on
# Render set CORS_ORIGINS to e.g. "https://sentinelx.vercel.app,http://localhost:5173".
_cors_raw = os.environ.get("CORS_ORIGINS", "*").strip()
CORS_ORIGINS = (
    ["*"] if _cors_raw == "*" else [o.strip() for o in _cors_raw.split(",") if o.strip()]
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.conn = _connect()
    try:
        yield
    finally:
        app.state.conn.close()


app = FastAPI(title="SentinelX I", version="0.6.5", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _maybe_json(s: str | None) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s


# --------------------------------------------------------------------------- #
# Health + meta
# --------------------------------------------------------------------------- #

@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/stats")
def stats() -> dict[str, Any]:
    c = app.state.conn
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
        "top_techniques": rows(
            "SELECT pt.technique_id, mt.name, COUNT(*) AS n FROM post_techniques pt "
            "LEFT JOIN mitre_techniques mt ON mt.technique_id = pt.technique_id "
            "GROUP BY pt.technique_id ORDER BY n DESC LIMIT 15"
        ),
    }


# --------------------------------------------------------------------------- #
# Posts
# --------------------------------------------------------------------------- #

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
    if category:
        where.append("rp.category = ?"); args.append(category)
    if intent:
        where.append("la.intent = ?"); args.append(intent)
    if technique:
        join_pt = "JOIN post_techniques pt ON pt.raw_post_id = rp.id AND pt.technique_id = ?"
        args.insert(0, technique)
    if q:
        where.append("(rp.body LIKE ? OR rp.thread_title LIKE ?)")
        like = f"%{q}%"; args.extend([like, like])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    count_sql = (
        f"SELECT COUNT(DISTINCT rp.id) AS n FROM raw_posts rp "
        f"LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id {join_pt} {where_sql}"
    )
    total = c.execute(count_sql, args).fetchone()["n"]

    list_sql = (
        f"SELECT DISTINCT rp.id, rp.thread_title, rp.category, rp.author, "
        f"  substr(rp.body, 1, 280) AS body_preview, rp.source_created_at, "
        f"  la.intent, la.summary "
        f"FROM raw_posts rp LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id {join_pt} "
        f"{where_sql} ORDER BY rp.source_created_at DESC LIMIT ? OFFSET ?"
    )
    items = [_row_to_dict(r) for r in c.execute(list_sql, args + [limit, offset])]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@app.get("/posts/{post_id}")
def get_post(post_id: int) -> dict[str, Any]:
    c = app.state.conn
    row = c.execute("SELECT * FROM raw_posts WHERE id = ?", (post_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"post {post_id} not found")
    post = _row_to_dict(row)

    la = c.execute("SELECT * FROM llm_analyses WHERE raw_post_id = ?", (post_id,)).fetchone()
    analysis: dict[str, Any] | None = None
    if la:
        analysis = _row_to_dict(la)
        analysis["targets"] = _maybe_json(analysis.pop("targets_json"))
        analysis["techniques"] = _maybe_json(analysis.pop("techniques_json"))
        analysis.pop("raw_responses", None)

    iocs = [_row_to_dict(r) for r in c.execute(
        "SELECT ioc_type, value, span_start, span_end FROM iocs WHERE raw_post_id = ? "
        "ORDER BY ioc_type, value", (post_id,))]
    entities = [_row_to_dict(r) for r in c.execute(
        "SELECT label, text, span_start, span_end FROM entities WHERE raw_post_id = ? "
        "ORDER BY label, text", (post_id,))]
    techniques = [_row_to_dict(r) for r in c.execute(
        "SELECT pt.technique_id, pt.source, pt.score, pt.evidence, "
        "       mt.name, mt.tactics, mt.url, mt.is_subtechnique, mt.parent_id "
        "FROM post_techniques pt LEFT JOIN mitre_techniques mt "
        "  ON mt.technique_id = pt.technique_id "
        "WHERE pt.raw_post_id = ? "
        "ORDER BY CASE pt.source WHEN 'llm_verified' THEN 0 WHEN 'semantic' THEN 1 ELSE 2 END, "
        "         pt.score DESC NULLS LAST, pt.technique_id", (post_id,))]
    for t in techniques:
        if t.get("tactics"):
            t["tactics"] = [x for x in t["tactics"].split(",") if x]

    return {"post": post, "analysis": analysis, "iocs": iocs,
            "entities": entities, "techniques": techniques}


# --------------------------------------------------------------------------- #
# MITRE
# --------------------------------------------------------------------------- #

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
    for it in items:
        it["post_ids"] = [int(x) for x in (it["post_ids"] or "").split(",") if x]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


# --------------------------------------------------------------------------- #
# Investigations + lenses (Stage 6.5)
# --------------------------------------------------------------------------- #

@app.get("/lenses")
def get_lenses() -> dict[str, Any]:
    return {"items": list_lenses()}


@app.get("/investigations")
def list_investigations() -> dict[str, Any]:
    return {"items": inv.list_investigations(app.state.conn)}


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


@app.get("/investigations/{investigation_id}")
def get_investigation(investigation_id: int) -> dict[str, Any]:
    try:
        return inv.get_investigation(app.state.conn, investigation_id)
    except KeyError:
        raise HTTPException(404, f"investigation {investigation_id} not found")


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


@app.delete("/investigations/{investigation_id}")
def delete_investigation(investigation_id: int) -> Response:
    if not inv.delete_investigation(app.state.conn, investigation_id):
        raise HTTPException(404, f"investigation {investigation_id} not found")
    return Response(status_code=204)


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
    """Deeper diagnostics for the dashboard. Each probe is independent."""
    import socket
    import time as _time

    c = app.state.conn
    out: dict[str, Any] = {"status": "ok", "checks": {}}

    # DB: a trivial query proves the connection is live.
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

    # Tor SOCKS5: TCP probe only — we don't actually open a circuit.
    t0 = _time.perf_counter()
    try:
        with socket.create_connection(("127.0.0.1", 9050), timeout=2.0):
            out["checks"]["tor_socks"] = {
                "status": "up",
                "latency_ms": int((_time.perf_counter() - t0) * 1000),
            }
    except Exception as e:
        out["checks"]["tor_socks"] = {"status": "down", "error": str(e)}

    # Ollama: the OllamaClient.health() call lists installed models.
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

    # Pipeline cursor state: how many posts are pending at each stage.
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
# Server-sent events (live timeline feed)
# --------------------------------------------------------------------------- #

# Snapshot of a post for the SSE wire format. Kept tight on purpose — the
# timeline only needs enough to draw a node and its first technique edges.
def _post_event(c: sqlite3.Connection, post_id: int) -> dict[str, Any]:
    row = c.execute(
        "SELECT rp.id, rp.thread_title, rp.category, rp.author, "
        "  substr(rp.body, 1, 240) AS body_preview, rp.source_created_at, "
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
    out["techniques"] = [
        {"technique_id": r["technique_id"], "source": r["source"], "name": r["name"]}
        for r in techs
    ]
    iocs = c.execute(
        "SELECT ioc_type, value FROM iocs WHERE raw_post_id = ? "
        "ORDER BY ioc_type LIMIT 8",
        (post_id,),
    ).fetchall()
    out["iocs"] = [{"ioc_type": r["ioc_type"], "value": r["value"]} for r in iocs]
    return out


@app.get("/events")
async def events_stream(request: Request, since_id: int = Query(0, ge=0)):
    """Server-sent stream of newly-ingested posts.

    Implementation note: we poll SQLite every 2s for `id > since_id` rather
    than wiring a real pub-sub. Reasons: (1) the scraper is the sole writer
    and we don't want the API process tapping into its connection; (2) at
    SentinelX's scale (one post every few seconds at most), polling cost is
    invisible; (3) SSE clients survive transient hiccups via EventSource's
    built-in reconnect.

    Frame types:
        event: hello   data: {"latest_id": N}              # on connect
        event: post    data: {"id": N, ...post snapshot}   # per new post
        event: ping    data: {"t": <epoch>}                # keepalive every 15s
    """

    async def gen():
        c = app.state.conn
        # since_id == 0 (the default) means "replay everything from the
        # start" — the timeline page uses this to bootstrap. Any positive
        # value is treated as "resume after this id".
        last = since_id
        latest = c.execute(
            "SELECT COALESCE(MAX(id), 0) FROM raw_posts"
        ).fetchone()[0]
        yield f"event: hello\ndata: {json.dumps({'latest_id': latest})}\n\n"

        last_ping = asyncio.get_event_loop().time()
        while True:
            if await request.is_disconnected():
                break

            rows = c.execute(
                "SELECT id FROM raw_posts WHERE id > ? ORDER BY id ASC LIMIT 50",
                (last,),
            ).fetchall()
            for row in rows:
                payload = _post_event(c, row["id"])
                yield f"event: post\ndata: {json.dumps(payload)}\n\n"
                last = row["id"]

            now = asyncio.get_event_loop().time()
            if now - last_ping > 15:
                yield f"event: ping\ndata: {json.dumps({'t': now})}\n\n"
                last_ping = now

            await asyncio.sleep(2.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx-style buffering if any
        },
    )


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
