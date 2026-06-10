"""PDF export for investigations.

Renders an investigation as a single styled PDF: cover page with metadata and
the lens summary (with [#NNN] citations turned into footnote-style references),
a MITRE coverage chart summarising the matched post set, and one appendix page
per cited post containing the full body, IOCs, entities, and technique map.

Implementation: HTML + CSS rendered via WeasyPrint. We build the HTML in
Python so we don't need a template engine — the structure is fixed.
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from backend.api import investigations as inv

CITATION_RE = re.compile(r"\[#(\d+)\]")


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _fmt_ts(ts: float | int | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fetch_full_post(conn: sqlite3.Connection, post_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT rp.id, rp.thread_title, rp.category, rp.author, rp.body, "
        "  rp.source_created_at, la.intent, la.summary "
        "FROM raw_posts rp LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id "
        "WHERE rp.id = ?",
        (post_id,),
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["iocs"] = [
        dict(r) for r in conn.execute(
            "SELECT ioc_type, value FROM iocs WHERE raw_post_id = ? "
            "ORDER BY ioc_type, value",
            (post_id,),
        )
    ]
    out["entities"] = [
        dict(r) for r in conn.execute(
            "SELECT label, text FROM entities WHERE raw_post_id = ? "
            "ORDER BY label, text",
            (post_id,),
        )
    ]
    out["techniques"] = [
        dict(r) for r in conn.execute(
            "SELECT pt.technique_id, pt.source, mt.name "
            "FROM post_techniques pt LEFT JOIN mitre_techniques mt "
            "  ON mt.technique_id = pt.technique_id "
            "WHERE pt.raw_post_id = ? "
            "ORDER BY CASE pt.source WHEN 'llm_verified' THEN 0 "
            "  WHEN 'semantic' THEN 1 ELSE 2 END, pt.technique_id",
            (post_id,),
        )
    ]
    return out


def _summary_with_footnotes(summary: str, post_ids: list[int]) -> tuple[str, list[int]]:
    """Replace [#NNN] in the summary with a numbered footnote anchor.

    Returns (html_summary, ordered_unique_ids) where the footnote numbers map
    to the order of first appearance and align with the appendix sections.
    """
    seen: dict[int, int] = {}
    order: list[int] = []

    def repl(m: re.Match) -> str:
        pid = int(m.group(1))
        if pid not in seen:
            order.append(pid)
            seen[pid] = len(order)
        n = seen[pid]
        return (
            f'<sup class="cite"><a href="#post-{pid}">[{n}]</a></sup>'
        )

    html_body = CITATION_RE.sub(repl, _esc(summary))
    html_body = html_body.replace("\n", "<br/>")
    # Ensure every cited post is in the appendix even if [#NNN] was missing
    # for some matched posts (they still belong to the investigation).
    for pid in post_ids:
        if pid not in seen:
            order.append(pid)
            seen[pid] = len(order)
    return html_body, order


def _build_mitre_chart_html(posts: list[dict[str, Any]]) -> str:
    counts: dict[str, dict[str, Any]] = {}
    for p in posts:
        for t in p.get("techniques") or []:
            tid = t["technique_id"]
            d = counts.setdefault(tid, {"name": t.get("name") or "", "n": 0})
            d["n"] += 1
            if not d["name"] and t.get("name"):
                d["name"] = t["name"]
    if not counts:
        return '<p class="muted">No MITRE techniques mapped to the cited posts.</p>'
    rows = sorted(counts.items(), key=lambda kv: (-kv[1]["n"], kv[0]))
    top = max(d["n"] for _, d in rows)
    parts = ['<table class="mitre">']
    parts.append(
        "<thead><tr><th>Technique</th><th>Name</th>"
        "<th class='count'>Posts</th><th class='bar'></th></tr></thead><tbody>"
    )
    for tid, d in rows:
        pct = int(round(100 * d["n"] / top)) if top else 0
        parts.append(
            f"<tr><td class='tid'>{_esc(tid)}</td>"
            f"<td>{_esc(d['name'])}</td>"
            f"<td class='count'>{d['n']}</td>"
            f"<td class='bar'><div class='bar-fill' style='width:{pct}%'></div></td></tr>"
        )
    parts.append("</tbody></table>")
    return "".join(parts)


def _build_mitigations_html(
    conn: sqlite3.Connection, post_ids: list[int]
) -> str:
    """Render the 'Recommended Actions' table: MITRE mitigations for the cited
    posts, ranked by how many posts each one would help defend."""
    mitigations = inv.aggregate_mitigations(conn, post_ids)
    if not mitigations:
        return ('<p class="muted">No MITRE mitigations are published for the '
                "techniques mapped to the cited posts.</p>")
    top = max(m["posts_covered"] for m in mitigations) or 1
    parts = ['<table class="mitre">']
    parts.append(
        "<thead><tr><th>Mitigation</th><th>Name</th>"
        "<th class='count'>Posts</th><th class='bar'></th></tr></thead><tbody>"
    )
    for m in mitigations:
        pct = int(round(100 * m["posts_covered"] / top))
        parts.append(
            f"<tr><td class='tid'>{_esc(m['mitigation_id'])}</td>"
            f"<td>{_esc(m['name'])}</td>"
            f"<td class='count'>{m['posts_covered']}</td>"
            f"<td class='bar'><div class='bar-fill' style='width:{pct}%'></div></td></tr>"
        )
    parts.append("</tbody></table>")
    return "".join(parts)


def _build_post_appendix_html(n: int, p: dict[str, Any]) -> str:
    body_html = _esc(p.get("body") or "").replace("\n", "<br/>")
    iocs = p.get("iocs") or []
    ents = p.get("entities") or []
    techs = p.get("techniques") or []

    iocs_html = (
        "<ul class='kv'>"
        + "".join(
            f"<li><span class='k'>{_esc(r['ioc_type'])}</span>"
            f"<span class='v'>{_esc(r['value'])}</span></li>"
            for r in iocs
        )
        + "</ul>"
    ) if iocs else "<p class='muted'>None.</p>"

    ents_html = (
        "<ul class='kv'>"
        + "".join(
            f"<li><span class='k'>{_esc(r['label'])}</span>"
            f"<span class='v'>{_esc(r['text'])}</span></li>"
            for r in ents
        )
        + "</ul>"
    ) if ents else "<p class='muted'>None.</p>"

    techs_html = (
        "<ul class='techs'>"
        + "".join(
            f"<li><span class='tid'>{_esc(r['technique_id'])}</span> "
            f"{_esc(r.get('name') or '')} "
            f"<span class='src src-{_esc(r['source'])}'>{_esc(r['source'])}</span></li>"
            for r in techs
        )
        + "</ul>"
    ) if techs else "<p class='muted'>None.</p>"

    return f"""
    <section class="appendix" id="post-{p['id']}">
      <h2>[{n}] Post #{p['id']} — {_esc(p.get('thread_title') or '')}</h2>
      <p class="meta">
        <span>Category: <b>{_esc(p.get('category') or '—')}</b></span> ·
        <span>Author: <b>{_esc(p.get('author') or '—')}</b></span> ·
        <span>Posted: <b>{_fmt_ts(p.get('source_created_at'))}</b></span> ·
        <span>Intent: <b>{_esc(p.get('intent') or '—')}</b></span>
      </p>
      {f"<p class='prior'><i>Prior summary:</i> {_esc(p['summary'])}</p>" if p.get('summary') else ''}
      <h3>Body</h3>
      <div class="body">{body_html}</div>
      <h3>IOCs</h3>{iocs_html}
      <h3>Entities</h3>{ents_html}
      <h3>MITRE techniques</h3>{techs_html}
    </section>
    """


_PDF_CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
@page { @bottom-right { content: "SentinelX I  ·  page " counter(page) " / " counter(pages); font-family: 'Inter', sans-serif; font-size: 9pt; color: #6b6388; } }
* { box-sizing: border-box; }
html, body { font-family: 'Inter', 'Segoe UI', sans-serif; color: #1c1730; font-size: 10.5pt; line-height: 1.45; }
h1 { font-size: 22pt; margin: 0 0 4pt 0; color: #2a1c5c; letter-spacing: -0.01em; }
h2 { font-size: 14pt; margin: 18pt 0 6pt 0; color: #2a1c5c; border-bottom: 1px solid #d8d2ee; padding-bottom: 3pt; }
h3 { font-size: 11pt; margin: 10pt 0 4pt 0; color: #4b3a8a; text-transform: uppercase; letter-spacing: 0.04em; }
.cover { page-break-after: always; }
.cover .brand { color: #6f4ad8; font-weight: 600; letter-spacing: 0.18em; font-size: 9pt; text-transform: uppercase; }
.cover .title { font-size: 26pt; font-weight: 700; color: #1c1730; margin: 6pt 0; }
.cover .desc { color: #4b3a8a; margin: 0 0 14pt 0; font-style: italic; }
.meta-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6pt 18pt; margin: 10pt 0 18pt 0; padding: 10pt 12pt; background: #f4f0ff; border-left: 3px solid #6f4ad8; }
.meta-grid .label { color: #6b6388; font-size: 8.5pt; text-transform: uppercase; letter-spacing: 0.06em; }
.meta-grid .value { color: #1c1730; font-weight: 600; font-size: 10pt; word-break: break-word; }
.summary { background: #ffffff; padding: 10pt 12pt; border: 1px solid #e6def5; border-radius: 4pt; }
sup.cite a { color: #6f4ad8; text-decoration: none; font-weight: 600; }
table.mitre { width: 100%; border-collapse: collapse; margin-top: 8pt; }
table.mitre th { text-align: left; font-size: 9pt; color: #6b6388; text-transform: uppercase; letter-spacing: 0.04em; border-bottom: 1px solid #d8d2ee; padding: 4pt 6pt; }
table.mitre td { padding: 4pt 6pt; border-bottom: 1px dotted #ece8f8; vertical-align: middle; font-size: 9.5pt; }
table.mitre td.tid { font-family: 'JetBrains Mono', 'Consolas', monospace; color: #6f4ad8; font-weight: 600; }
table.mitre td.count { text-align: right; font-variant-numeric: tabular-nums; width: 40pt; color: #4b3a8a; }
table.mitre td.bar { width: 35%; }
table.mitre .bar-fill { height: 6pt; background: linear-gradient(90deg, #6f4ad8, #b89bff); border-radius: 3pt; }
.appendix { page-break-before: always; }
.appendix .meta { color: #6b6388; font-size: 9pt; margin: 0 0 8pt 0; }
.appendix .meta b { color: #2a1c5c; }
.appendix .prior { background: #faf7ff; padding: 6pt 8pt; border-left: 2px solid #b89bff; font-size: 9.5pt; margin: 4pt 0 8pt 0; }
.appendix .body { white-space: normal; font-size: 10pt; padding: 6pt 8pt; background: #fbfaff; border: 1px solid #ece8f8; border-radius: 3pt; }
ul.kv { list-style: none; padding: 0; margin: 4pt 0; columns: 2; column-gap: 16pt; }
ul.kv li { font-size: 9.5pt; padding: 1pt 0; break-inside: avoid; }
ul.kv .k { display: inline-block; min-width: 56pt; color: #6b6388; text-transform: uppercase; font-size: 8pt; letter-spacing: 0.05em; }
ul.kv .v { font-family: 'JetBrains Mono', 'Consolas', monospace; color: #1c1730; word-break: break-all; }
ul.techs { list-style: none; padding: 0; margin: 4pt 0; }
ul.techs li { font-size: 9.5pt; padding: 2pt 0; }
ul.techs .tid { font-family: 'JetBrains Mono', 'Consolas', monospace; color: #6f4ad8; font-weight: 600; margin-right: 4pt; }
.src { font-size: 7.5pt; padding: 1pt 5pt; border-radius: 8pt; margin-left: 4pt; text-transform: uppercase; letter-spacing: 0.04em; }
.src-llm_verified { background: #e2d8ff; color: #3d2978; }
.src-semantic { background: #d6efe6; color: #1f5e3f; }
.src-llm_unverified { background: #fde4d0; color: #6b3a17; }
.muted { color: #8a82a8; font-style: italic; }
"""


def render_investigation_pdf(
    conn: sqlite3.Connection, investigation_id: int
) -> tuple[bytes, str]:
    """Render the investigation to a PDF. Returns (bytes, suggested_filename)."""
    investigation = inv.get_investigation(conn, investigation_id, include_posts=False)

    summary_text = (investigation.get("summary") or "").strip()
    summary_post_ids = investigation.get("summary_post_ids") or []

    if not summary_text:
        # Allow exporting even if the lens has not been run yet — produce a
        # filter-only report. The matched-post set is then used directly.
        matched = inv.evaluate_filter(conn, investigation["filters"], limit=50)
        summary_post_ids = [int(r["id"]) for r in matched]
        summary_html = '<p class="muted">No lens summary has been generated yet. Rerun the investigation to populate this section.</p>'
        ordered_ids = summary_post_ids
    else:
        summary_html, ordered_ids = _summary_with_footnotes(summary_text, summary_post_ids)

    # Pull full post records for the appendix (and for the chart).
    full_posts: list[dict[str, Any]] = []
    for pid in ordered_ids:
        full = _fetch_full_post(conn, pid)
        if full:
            full_posts.append(full)

    chart_html = _build_mitre_chart_html(full_posts)
    mitigations_html = _build_mitigations_html(conn, [p["id"] for p in full_posts])

    filters_pretty = _esc(json.dumps(investigation["filters"], indent=2, sort_keys=True))

    appendices_html = "".join(
        _build_post_appendix_html(i + 1, p) for i, p in enumerate(full_posts)
    )

    generated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html_doc = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{_esc(investigation['name'])}</title>
<style>{_PDF_CSS}</style></head><body>
<section class="cover">
  <div class="brand">SentinelX I — Investigation Report</div>
  <h1 class="title">{_esc(investigation['name'])}</h1>
  {f"<p class='desc'>{_esc(investigation['description'])}</p>" if investigation.get('description') else ''}
  <div class="meta-grid">
    <div><div class="label">Investigation ID</div><div class="value">#{investigation['id']}</div></div>
    <div><div class="label">Lens</div><div class="value">{_esc(investigation.get('lens') or '—')}</div></div>
    <div><div class="label">Created</div><div class="value">{_fmt_ts(investigation.get('created_at'))}</div></div>
    <div><div class="label">Last rerun</div><div class="value">{_fmt_ts(investigation.get('last_run_at'))}</div></div>
    <div><div class="label">Summary model</div><div class="value">{_esc(investigation.get('summary_model') or '—')}</div></div>
    <div><div class="label">Generated</div><div class="value">{generated_at}</div></div>
  </div>

  <h2>Filter</h2>
  <pre style="background:#1c1730;color:#e8e3fb;padding:8pt 10pt;border-radius:4pt;font-size:9pt;font-family:'JetBrains Mono','Consolas',monospace;white-space:pre-wrap;">{filters_pretty}</pre>

  <h2>Lens summary</h2>
  <div class="summary">{summary_html}</div>

  <h2>MITRE ATT&amp;CK coverage</h2>
  {chart_html}

  <h2>Recommended actions</h2>
  <p class="muted">Official MITRE ATT&amp;CK mitigations for the techniques mapped to the cited posts, ranked by how many posts each one would help defend. Address the highest-coverage items first.</p>
  {mitigations_html}
</section>

{appendices_html}

</body></html>
"""

    # Import lazily so missing GTK runtime doesn't break the rest of the API.
    from weasyprint import HTML  # type: ignore

    pdf_bytes = HTML(string=html_doc).write_pdf()
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", investigation["name"]).strip("_") or "investigation"
    filename = f"sentinelx_{investigation['id']}_{safe_name}.pdf"
    return pdf_bytes, filename
