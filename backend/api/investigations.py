"""Investigations: saved filter views with optional cross-post LLM summaries.

An investigation = (name, filters_json, optional lens, optional summary).
The filter JSON is stored verbatim and re-evaluated on every read, so an
investigation reflects the current corpus, not a snapshot at creation time.

Filter schema (all keys optional, all combine with AND):
    {
      "category": "ransomware",
      "intent": "sale",
      "technique": "T1566",
      "q": "credential",
      "ioc_type": "btc",
      "since": 1745000000.0,
      "until": 1746000000.0,
      "post_ids": [1, 2, 3]
    }

A rerun packs every matching post's body + IOCs + entities + technique list
into a single prompt, asks Mistral to write a fused summary under the
chosen lens, and writes it back to investigations.summary.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from backend.llm.client import OllamaClient
from backend.llm.lenses import get_lens

# Cap on how many posts get fed into a single rerun prompt. Mistral's effective
# context for our setup is ~8k tokens; at ~400 tokens of body+enrichment per
# post we top out around 20 before quality degrades. The cap is per-rerun, not
# per-investigation — bigger investigations just sample.
MAX_POSTS_PER_RERUN = 20
MAX_BODY_CHARS = 1200


def _build_where(filters: dict) -> tuple[str, list[Any], str]:
    """Translate the filter dict to a SQL WHERE + JOIN clause.

    Returns (where_sql, args, join_sql). The caller is responsible for the
    SELECT/FROM and ordering. We always alias raw_posts as rp.
    """
    where: list[str] = []
    where_args: list[Any] = []
    joins: list[str] = []
    join_args: list[Any] = []

    if filters.get("category"):
        where.append("rp.category = ?")
        where_args.append(filters["category"])
    if filters.get("intent"):
        joins.append("LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id")
        where.append("la.intent = ?")
        where_args.append(filters["intent"])
    if filters.get("technique"):
        joins.append(
            "JOIN post_techniques pt_f ON pt_f.raw_post_id = rp.id "
            "AND pt_f.technique_id = ?"
        )
        join_args.append(str(filters["technique"]).upper())
    if filters.get("q"):
        where.append("(rp.body LIKE ? OR rp.thread_title LIKE ?)")
        like = f"%{filters['q']}%"
        where_args.extend([like, like])
    if filters.get("ioc_type"):
        joins.append(
            "JOIN iocs ioc_f ON ioc_f.raw_post_id = rp.id AND ioc_f.ioc_type = ?"
        )
        join_args.append(filters["ioc_type"])
    if filters.get("since") is not None:
        where.append("rp.source_created_at >= ?")
        where_args.append(float(filters["since"]))
    if filters.get("until") is not None:
        where.append("rp.source_created_at <= ?")
        where_args.append(float(filters["until"]))
    if filters.get("post_ids"):
        ids = [int(x) for x in filters["post_ids"]]
        if not ids:
            where.append("0=1")
        else:
            placeholders = ",".join("?" for _ in ids)
            where.append(f"rp.id IN ({placeholders})")
            where_args.extend(ids)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    join_sql = " ".join(dict.fromkeys(joins))  # dedupe while preserving order
    return where_sql, join_args + where_args, join_sql


def evaluate_filter(
    conn: sqlite3.Connection,
    filters: dict,
    *,
    limit: int | None = None,
) -> list[dict]:
    """Return the post rows matching `filters`, newest first.

    Each row carries the post + analysis fields the dashboard needs in the
    list view. The full body / IOCs / entities are not joined here — the
    rerun helper pulls those separately for the small sampled subset.
    """
    where_sql, args, join_sql = _build_where(filters or {})
    if "LEFT JOIN llm_analyses la" not in join_sql:
        join_sql = (join_sql + " LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id").strip()
    sql = (
        "SELECT DISTINCT rp.id, rp.thread_title, rp.category, rp.author, "
        "  substr(rp.body, 1, 280) AS body_preview, rp.source_created_at, "
        "  la.intent, la.summary "
        f"FROM raw_posts rp {join_sql} {where_sql} "
        "ORDER BY rp.source_created_at DESC"
    )
    if limit is not None:
        sql += " LIMIT ?"
        args = args + [limit]
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


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
    """Render one post + its enrichment as a compact text block for the LLM."""
    p = conn.execute(
        "SELECT id, thread_title, category, author, body, source_created_at "
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

    body = (p["body"] or "")[:MAX_BODY_CHARS]
    parts = [
        f"=== POST [#{p['id']}] === ({p['category']} / {p['author']})",
        f"TITLE: {p['thread_title']}",
        f"BODY: {body}",
    ]
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
    """Re-evaluate the filter, pack the matched posts, run the lens prompt.

    Writes summary + summary_model + summary_post_ids + last_run_at + updated_at
    back onto the investigation row. Returns the updated row as a dict.
    """
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

    rows = evaluate_filter(conn, filters, limit=MAX_POSTS_PER_RERUN)
    if not rows:
        summary = (
            "No posts matched this investigation's filter at the time of the "
            "rerun. Loosen the filter or wait for new ingestion."
        )
        post_ids: list[int] = []
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
                system=lens.system,
                json_mode=False,
                temperature=0.2,
                num_predict=1500,
            )
        summary = gen.text.strip()

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
    """Build a priority-ranked mitigation list across a set of posts.

    For every MITRE mitigation reachable from any technique on any of these
    posts, count how many distinct posts it would help defend. The result is a
    'do this first' list — the mitigation covering the most posts ranks highest.

    Pure lookup over post_techniques -> technique_mitigations; no LLM.
    """
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
