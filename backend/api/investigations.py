"""Investigations: saved filters (re-evaluated live) + optional LLM summary through a lens.
Filter keys (all optional, ANDed): category, intent, technique, q, ioc_type, since, until, post_ids
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from backend.llm.client import OllamaClient
from backend.llm.lenses import get_lens
from backend.llm.prompts import UNTRUSTED_END, UNTRUSTED_RULE, UNTRUSTED_START, fence_untrusted

# Max posts sent to the LLM per rerun (Mistral context is ~8k tokens)
MAX_POSTS_PER_RERUN = 20
# Each post body is cut to this length in the lens prompt
MAX_BODY_CHARS = 1200


def _build_where(filters: dict) -> tuple[str, list[Any], str]:
    """Turn the filter dict into SQL (where, args, join); raw_posts is aliased rp."""
    # Collect WHERE conditions and JOINs (with their ? values) for each filter that is set
    where: list[str] = []
    where_args: list[Any] = []
    joins: list[str] = []
    join_args: list[Any] = []

    # Filter by forum category
    if filters.get("category"):
        where.append("rp.category = ?")
        where_args.append(filters["category"])
    # Filter by LLM intent (needs the llm_analyses table)
    if filters.get("intent"):
        joins.append("LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id")
        where.append("la.intent = ?")
        where_args.append(filters["intent"])
    # Filter by MITRE technique
    if filters.get("technique"):
        joins.append(
            "JOIN post_techniques pt_f ON pt_f.raw_post_id = rp.id "
            "AND pt_f.technique_id = ?"
        )
        join_args.append(str(filters["technique"]).upper())
    if filters.get("q"):
        # Search the English translation too, matching /posts.
        where.append("(rp.body LIKE ? OR rp.body_en LIKE ? OR rp.thread_title LIKE ?)")
        like = f"%{filters['q']}%"
        where_args.extend([like, like, like])
    # Only posts that contain a certain IOC type (e.g. btc)
    if filters.get("ioc_type"):
        joins.append(
            "JOIN iocs ioc_f ON ioc_f.raw_post_id = rp.id AND ioc_f.ioc_type = ?"
        )
        join_args.append(filters["ioc_type"])
    # Date range
    if filters.get("since") is not None:
        where.append("rp.source_created_at >= ?")
        where_args.append(float(filters["since"]))
    if filters.get("until") is not None:
        where.append("rp.source_created_at <= ?")
        where_args.append(float(filters["until"]))
    # A fixed list of post ids
    if filters.get("post_ids"):
        ids = [int(x) for x in filters["post_ids"]]
        if not ids:
            where.append("0=1")
        else:
            placeholders = ",".join("?" for _ in ids)
            where.append(f"rp.id IN ({placeholders})")
            where_args.extend(ids)

    # Glue everything together into SQL text
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    join_sql = " ".join(dict.fromkeys(joins))  # dedupe while preserving order
    return where_sql, join_args + where_args, join_sql


def evaluate_filter(
    conn: sqlite3.Connection,
    filters: dict,
    *,
    limit: int | None = None,
) -> list[dict]:
    """Posts matching the filters, newest first."""
    where_sql, args, join_sql = _build_where(filters or {})
    # Always join llm_analyses so intent + summary come back too
    if "LEFT JOIN llm_analyses la" not in join_sql:
        join_sql = (join_sql + " LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id").strip()
    sql = (
        "SELECT DISTINCT rp.id, rp.thread_title, rp.category, rp.author, "
        "  substr(rp.body, 1, 280) AS body_preview, rp.source_created_at, "
        "  la.intent, la.summary "
        f"FROM raw_posts rp {join_sql} {where_sql} "
        "ORDER BY rp.source_created_at DESC"
    )
    # Optional row limit
    if limit is not None:
        sql += " LIMIT ?"
        args = args + [limit]
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


# Same filters, but only count the posts
def count_filter(conn: sqlite3.Connection, filters: dict) -> int:
    where_sql, args, join_sql = _build_where(filters or {})
    if "LEFT JOIN llm_analyses la" not in join_sql:
        join_sql = (join_sql + " LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id").strip()
    sql = (
        f"SELECT COUNT(DISTINCT rp.id) AS n "
        f"FROM raw_posts rp {join_sql} {where_sql}"
    )
    return conn.execute(sql, args).fetchone()["n"]


def _pack_post_for_lens(conn: sqlite3.Connection, post_id: int) -> str:
    # Load the post, its LLM analysis, IOCs, entities and techniques
    """Render one post + its enrichment as a compact text block for the LLM."""
    p = conn.execute(
        # Use the English translation so the LLM reads English
        "SELECT id, thread_title, category, author, "
        "  COALESCE(body_en, body) AS body, source_created_at "
        "FROM raw_posts WHERE id = ?", (post_id,)
    ).fetchone()
    if not p:
        return ""
    la = conn.execute(
        "SELECT intent, summary, targets_json, techniques_json "
        "FROM llm_analyses WHERE raw_post_id = ?", (post_id,)
    ).fetchone()
    iocs = conn.execute(
        "SELECT ioc_type, value FROM iocs WHERE raw_post_id = ? "
        "ORDER BY ioc_type", (post_id,)
    ).fetchall()
    ents = conn.execute(
        "SELECT label, text FROM entities WHERE raw_post_id = ? "
        "ORDER BY label", (post_id,)
    ).fetchall()
    techs = conn.execute(
        "SELECT pt.technique_id, mt.name FROM post_techniques pt "
        "LEFT JOIN mitre_techniques mt ON mt.technique_id = pt.technique_id "
        "WHERE pt.raw_post_id = ? ORDER BY pt.source, pt.technique_id",
        (post_id,)
    ).fetchall()

    # Forum text is wrapped in the untrusted markers
    body = (p["body"] or "")[:MAX_BODY_CHARS]
    # Only forum-written text is untrusted (fenced); our own enrichment stays outside
    parts = [
        f"=== POST [#{p['id']}] ===",
        UNTRUSTED_START,
        f"CATEGORY/AUTHOR: {fence_untrusted(p['category'])} / {fence_untrusted(p['author'])}",
        f"TITLE: {fence_untrusted(p['thread_title'])}",
        f"BODY: {fence_untrusted(body)}",
        UNTRUSTED_END,
    ]
    # Then add what our pipeline already found
    if la:
        if la["intent"]:
            parts.append(f"INTENT: {la['intent']}")
        if la["summary"]:
            parts.append(f"PRIOR SUMMARY: {la['summary']}")
    if iocs:
        parts.append("IOCS: " + "; ".join(f"{r['ioc_type']}={r['value']}" for r in iocs))
    if ents:
        parts.append("ENTITIES: " + "; ".join(f"{r['label']}={r['text']}" for r in ents))
    if techs:
        parts.append(
            "MITRE: " + "; ".join(
                f"{r['technique_id']}" + (f" {r['name']}" if r["name"] else "")
                for r in techs
            )
        )
    return "\n".join(parts)


def run_lens_summary(
    conn: sqlite3.Connection,
    investigation_id: int,
    *,
    model: str = "mistral",
) -> dict[str, Any]:
    """Re-run the filter, send matched posts through the lens prompt, save the summary."""
    # Load the investigation and its lens (it must have one)
    inv = conn.execute(
        "SELECT * FROM investigations WHERE id = ?", (investigation_id,)
    ).fetchone()
    if inv is None:
        raise KeyError(f"investigation {investigation_id} not found")
    if not inv["lens"]:
        raise ValueError(
            f"investigation {investigation_id} has no lens set; "
            "cannot run a summary"
        )
    lens = get_lens(inv["lens"])
    filters = json.loads(inv["filters_json"] or "{}")

    # Find matching posts (capped so the prompt fits)
    rows = evaluate_filter(conn, filters, limit=MAX_POSTS_PER_RERUN)
    if not rows:
        summary = (
            "No posts matched this investigation's filter at the time of the "
            "rerun. Loosen the filter or wait for new ingestion."
        )
        post_ids: list[int] = []
    # Pack each post as text and ask the LLM for one combined report
    else:
        post_ids = [int(r["id"]) for r in rows]
        blocks = [_pack_post_for_lens(conn, pid) for pid in post_ids]
        prompt = (
            f"You are reviewing {len(blocks)} darknet forum posts for the "
            f"'{lens.label}' lens.\n\n"
            "Each post block is preceded by '=== POST [#id] ===' and contains "
            "the body plus already-extracted IOCs, entities, and MITRE technique "
            "mappings. Use the structured fields as authoritative; treat the "
            "body as supporting context.\n\n"
            "POSTS:\n\n" + "\n\n".join(blocks) +
            "\n\nNow produce the report exactly per the system instructions."
        )
        with OllamaClient(model=model) as cli:
            gen = cli.generate(
                prompt,
                system=lens.system + UNTRUSTED_RULE,
                json_mode=False,
                temperature=0.2,
                num_predict=1500,
            )
        summary = gen.text.strip()

    # Save the summary and which posts it was based on
    now = time.time()
    conn.execute(
        "UPDATE investigations SET summary = ?, summary_model = ?, "
        "  summary_post_ids = ?, last_run_at = ?, updated_at = ? "
        "WHERE id = ?",
        (summary, model, json.dumps(post_ids), now, now, investigation_id),
    )
    conn.commit()
    updated = conn.execute(
        "SELECT * FROM investigations WHERE id = ?", (investigation_id,)
    ).fetchone()
    return _row_to_inv(updated)


def _row_to_inv(row: sqlite3.Row) -> dict[str, Any]:
    # DB row -> dict, with the JSON columns decoded
    d = dict(row)
    d["filters"] = json.loads(d.pop("filters_json") or "{}")
    d["summary_post_ids"] = json.loads(d.get("summary_post_ids") or "[]")
    return d


def create_investigation(
    conn: sqlite3.Connection,
    *,
    name: str,
    description: str | None,
    filters: dict,
    lens: str | None,
) -> dict[str, Any]:
    # Make sure the lens name exists, then insert
    if lens is not None:
        get_lens(lens)  # validates
    now = time.time()
    cur = conn.execute(
        "INSERT INTO investigations (name, description, filters_json, lens, "
        "  created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, description, json.dumps(filters or {}), lens, now, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM investigations WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return _row_to_inv(row)


def update_investigation(
    conn: sqlite3.Connection,
    investigation_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    filters: dict | None = None,
    lens: str | None = None,
) -> dict[str, Any]:
    # Only update the fields that were given
    sets: list[str] = []
    args: list[Any] = []
    if name is not None:
        sets.append("name = ?"); args.append(name)
    if description is not None:
        sets.append("description = ?"); args.append(description)
    if filters is not None:
        sets.append("filters_json = ?"); args.append(json.dumps(filters))
    if lens is not None:
        get_lens(lens)
        sets.append("lens = ?"); args.append(lens)
    # Nothing to change: just return the current row
    if not sets:
        row = conn.execute(
            "SELECT * FROM investigations WHERE id = ?", (investigation_id,)
        ).fetchone()
        if row is None:
            raise KeyError(investigation_id)
        return _row_to_inv(row)
    sets.append("updated_at = ?"); args.append(time.time())
    args.append(investigation_id)
    conn.execute(
        f"UPDATE investigations SET {', '.join(sets)} WHERE id = ?", args
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM investigations WHERE id = ?", (investigation_id,)
    ).fetchone()
    if row is None:
        raise KeyError(investigation_id)
    return _row_to_inv(row)


def delete_investigation(conn: sqlite3.Connection, investigation_id: int) -> bool:
    # Delete; True if a row was actually removed
    cur = conn.execute(
        "DELETE FROM investigations WHERE id = ?", (investigation_id,)
    )
    conn.commit()
    return cur.rowcount > 0


def list_investigations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM investigations ORDER BY created_at DESC"
    ).fetchall()
    return [_row_to_inv(r) for r in rows]


def aggregate_mitigations(
    conn: sqlite3.Connection, post_ids: list[int]
) -> list[dict[str, Any]]:
    """Mitigations across these posts, ranked by how many posts each one covers."""
    # Every (mitigation, post, technique) link for these posts
    ids = [int(p) for p in post_ids]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT m.mitigation_id, m.name, m.description, m.url,
               pt.raw_post_id, tm.technique_id
        FROM post_techniques pt
        JOIN technique_mitigations tm ON tm.technique_id = pt.technique_id
        JOIN mitre_mitigations m ON m.mitigation_id = tm.mitigation_id
        WHERE pt.raw_post_id IN ({placeholders})
        """,
        ids,
    ).fetchall()

    # Group by mitigation, collecting which posts and techniques it covers
    by_mid: dict[str, dict[str, Any]] = {}
    for r in rows:
        mid = r["mitigation_id"]
        entry = by_mid.get(mid)
        if entry is None:
            entry = {
                "mitigation_id": mid,
                "name": r["name"],
                "description": r["description"],
                "url": r["url"],
                "_posts": set(),
                "_techniques": set(),
            }
            by_mid[mid] = entry
        entry["_posts"].add(r["raw_post_id"])
        entry["_techniques"].add(r["technique_id"])

    # Turn the sets into counts and sort: covers the most posts first
    total_posts = len(set(ids))
    out: list[dict[str, Any]] = []
    for e in by_mid.values():
        posts_covered = len(e.pop("_posts"))
        techniques = sorted(e.pop("_techniques"))
        out.append({
            **e,
            "posts_covered": posts_covered,
            "post_share": round(posts_covered / total_posts, 3) if total_posts else 0.0,
            "techniques": techniques,
        })
    out.sort(key=lambda e: (-e["posts_covered"], e["mitigation_id"]))
    return out


def get_investigation(
    conn: sqlite3.Connection, investigation_id: int, *, include_posts: bool = True
) -> dict[str, Any]:
    # Load the investigation; optionally run its filter and attach the matching posts + mitigations
    row = conn.execute(
        "SELECT * FROM investigations WHERE id = ?", (investigation_id,)
    ).fetchone()
    if row is None:
        raise KeyError(investigation_id)
    inv = _row_to_inv(row)
    if include_posts:
        posts = evaluate_filter(conn, inv["filters"], limit=200)
        inv["matched_posts"] = posts
        inv["matched_total"] = count_filter(conn, inv["filters"])
        inv["mitigations"] = aggregate_mitigations(
            conn, [int(p["id"]) for p in posts]
        )
    return inv
