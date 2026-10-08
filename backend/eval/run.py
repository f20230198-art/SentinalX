"""Evaluation harness: how accurate is SentinelX, measured, not claimed.

Three evaluations, written to backend/eval/EVAL_REPORT.md:

1. IOC extraction — on synthetic cases with ground truth by construction
   (ioc_cases.py). Per-type precision / recall / F1, plus an ablation showing
   what the Public-Suffix-List domain filter is worth.

2. Intent classification — the stored LLM intent vs the hand-labelled gold
   set (gold_posts.json): accuracy on all posts and on unambiguous ones,
   plus the confusion pairs.

3. MITRE technique mapping — micro precision / recall / F1 of four
   strategies on the gold set, all scored at parent-technique level:
     * LLM raw        every T-code Mistral proposed (incl. invalid ones)
     * LLM verified   only proposals that exist in the ATT&CK corpus
     * Semantic only  MiniLM cosine top-k over chunked posts, no LLM
     * Hybrid         verified ∪ semantic — what the pipeline stores
   plus a threshold sweep for the semantic matcher (why 0.45?).

No Ollama needed: LLM outputs are read from llm_analyses (already stored).
The embedding model must be available locally (HF cache).

Usage:
    python -m backend.eval.run
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from backend.eval.ioc_cases import build_cases
from backend.mitre import embed as embed_mod
from backend.mitre import match as match_mod
from backend.pipeline import extract as extract_mod

HERE = Path(__file__).resolve().parent
DB_PATH = HERE.parent / "db" / "sentinelx.db"
GOLD = HERE / "gold_posts.json"
REPORT = HERE / "EVAL_REPORT.md"

TOPK = 5
THRESHOLD = 0.45


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def fmt(x: float) -> str:
    return f"{x:.2f}"


# --------------------------------------------------------------------------- #
# 1. IOC extraction
# --------------------------------------------------------------------------- #

def eval_iocs(psl_filter: bool = True) -> tuple[dict[str, list[int]], list[str]]:
    """Returns per-type [tp, fp, fn] and up to 5 example false positives."""
    original = extract_mod._is_real_domain
    if not psl_filter:
        extract_mod._is_real_domain = lambda _c: True   # ablation: pre-fix behaviour
    try:
        ext = extract_mod.IOCExtractor()
        counts: dict[str, list[int]] = {}
        fps: list[str] = []
        for case in build_cases():
            got = {(m.type, m.value) for m in ext.extract(case.text)}
            for t, v in got | case.expected:
                c = counts.setdefault(t, [0, 0, 0])
                if (t, v) in got and (t, v) in case.expected:
                    c[0] += 1
                elif (t, v) in got:
                    c[1] += 1
                    if len(fps) < 5 and f"{t}={v}" not in fps:
                        fps.append(f"{t}={v}")
                else:
                    c[2] += 1
        return counts, fps
    finally:
        extract_mod._is_real_domain = original


# --------------------------------------------------------------------------- #
# 2 + 3. Intent and techniques on the gold set
# --------------------------------------------------------------------------- #

def parent(tcode: str) -> str:
    return tcode.split(".")[0]


def load_gold() -> list[dict]:
    return json.loads(GOLD.read_text(encoding="utf-8"))["posts"]


def eval_intent(conn: sqlite3.Connection, gold: list[dict]) -> dict:
    rows = {r["raw_post_id"]: r["intent"] for r in conn.execute(
        "SELECT raw_post_id, intent FROM llm_analyses")}
    total = correct = clear_total = clear_correct = 0
    confusions: Counter[tuple[str, str]] = Counter()
    for g in gold:
        pred = rows.get(g["id"])
        ok = pred == g["intent"]
        total += 1
        correct += ok
        if not g.get("ambiguous"):
            clear_total += 1
            clear_correct += ok
        if not ok:
            confusions[(g["intent"], str(pred))] += 1
    return {"total": total, "correct": correct,
            "clear_total": clear_total, "clear_correct": clear_correct,
            "confusions": confusions.most_common()}


def eval_techniques(conn: sqlite3.Connection, gold: list[dict]) -> dict:
    ids = [g["id"] for g in gold]
    corpus_ids, matrix, corpus_set = match_mod.load_corpus(conn)
    corpus_parents = {parent(t) for t in corpus_set}

    rows = {r["id"]: r for r in conn.execute(
        f"SELECT rp.id, COALESCE(rp.body_en, rp.body) AS body, la.techniques_json "
        f"FROM raw_posts rp LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id "
        f"WHERE rp.id IN ({','.join('?' * len(ids))})", ids)}

    # Embed every chunk of every gold post once; slice per post.
    chunks, bounds = [], []
    for pid in ids:
        parts = match_mod.chunk_text(rows[pid]["body"] or "")
        bounds.append((len(chunks), len(chunks) + len(parts)))
        chunks.extend(parts)
    vecs = embed_mod.encode(chunks)
    post_vecs = {pid: vecs[a:b] for pid, (a, b) in zip(ids, bounds)}

    def semantic(pid: int, thr: float, exclude: set[str]) -> set[str]:
        ms = match_mod.semantic_topk(post_vecs[pid], matrix, corpus_ids,
                                     topk=TOPK, threshold=thr, exclude=exclude)
        return {parent(m.technique_id) for m in ms}

    strategies = {"LLM raw": [0, 0, 0], "LLM verified": [0, 0, 0],
                  "Semantic only": [0, 0, 0], "Hybrid (stored by pipeline)": [0, 0, 0]}
    sweep = {t: [0, 0, 0] for t in (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60)}

    def score(acc: list[int], pred: set[str], truth: set[str]) -> None:
        acc[0] += len(pred & truth)
        acc[1] += len(pred - truth)
        acc[2] += len(truth - pred)

    for g in gold:
        pid, truth = g["id"], {parent(t) for t in g["techniques"]}
        cands = match_mod.parse_llm_candidates(rows[pid]["techniques_json"])
        llm = match_mod.verify_llm(cands, corpus_set)
        raw = {parent(m.technique_id) for m in llm}
        verified_full = {m.technique_id for m in llm if m.source == "llm_verified"}
        verified = {parent(t) for t in verified_full} & corpus_parents
        score(strategies["LLM raw"], raw, truth)
        score(strategies["LLM verified"], verified, truth)
        score(strategies["Semantic only"], semantic(pid, THRESHOLD, set()), truth)
        score(strategies["Hybrid (stored by pipeline)"],
              verified | semantic(pid, THRESHOLD, verified_full), truth)
        for thr, acc in sweep.items():
            score(acc, semantic(pid, thr, set()), truth)

    return {"strategies": strategies, "sweep": sweep}


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def main() -> int:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    gold = load_gold()

    ioc_after, fps_after = eval_iocs(psl_filter=True)
    ioc_before, fps_before = eval_iocs(psl_filter=False)
    intent = eval_intent(conn, gold)
    tech = eval_techniques(conn, gold)

    L: list[str] = []
    L.append("# SentinelX — Evaluation Report\n")
    L.append("Generated by `python -m backend.eval.run`. Re-run after any change to "
             "extraction, prompts, or matching.\n")
    L.append("> ⚠ Gold intent/technique labels in `gold_posts.json` are a DRAFT and "
             "must be reviewed by a human before these numbers are quoted.\n")

    L.append("## 1. IOC extraction (80 synthetic cases, ground truth by construction)\n")
    L.append("| Type | TP | FP | FN | Precision | Recall | F1 |")
    L.append("|---|---|---|---|---|---|---|")
    tot = [0, 0, 0]
    for t in sorted(ioc_after):
        tp, fp, fn = ioc_after[t]
        tot = [a + b for a, b in zip(tot, ioc_after[t])]
        p, r, f = prf(tp, fp, fn)
        L.append(f"| {t} | {tp} | {fp} | {fn} | {fmt(p)} | {fmt(r)} | {fmt(f)} |")
    p, r, f = prf(*tot)
    L.append(f"| **all** | {tot[0]} | {tot[1]} | {tot[2]} | **{fmt(p)}** | **{fmt(r)}** | **{fmt(f)}** |\n")
    if fps_after:
        L.append(f"Remaining false positives (examples): `{'`, `'.join(fps_after)}`\n")
    dp_b = prf(*ioc_before.get("domain", [0, 0, 0]))
    dp_a = prf(*ioc_after.get("domain", [0, 0, 0]))
    L.append(f"**Ablation — Public Suffix List domain filter:** domain precision "
             f"{fmt(dp_b[0])} → {fmt(dp_a[0])} (recall {fmt(dp_b[1])} → {fmt(dp_a[1])}). "
             f"False positives removed include: `{'`, `'.join(f for f in fps_before if f.startswith('domain'))}`.\n")

    L.append("## 2. Intent classification (stored Mistral output vs gold)\n")
    L.append(f"- All posts: **{intent['correct']}/{intent['total']} = "
             f"{fmt(intent['correct'] / intent['total'])}**")
    L.append(f"- Unambiguous posts only: **{intent['clear_correct']}/{intent['clear_total']} = "
             f"{fmt(intent['clear_correct'] / intent['clear_total'])}**")
    if intent["confusions"]:
        L.append("- Errors (gold → predicted): " + ", ".join(
            f"{g}→{p} ×{n}" for (g, p), n in intent["confusions"]))
    L.append("")

    L.append(f"## 3. MITRE technique mapping ({len(gold)} gold posts, parent-technique level)\n")
    L.append("| Strategy | TP | FP | FN | Precision | Recall | F1 |")
    L.append("|---|---|---|---|---|---|---|")
    for name, (tp, fp, fn) in tech["strategies"].items():
        p, r, f = prf(tp, fp, fn)
        L.append(f"| {name} | {tp} | {fp} | {fn} | {fmt(p)} | {fmt(r)} | {fmt(f)} |")
    L.append("")
    L.append(f"Semantic matcher threshold sweep (top-k={TOPK}, no LLM):\n")
    L.append("| Threshold | Precision | Recall | F1 |")
    L.append("|---|---|---|---|")
    for thr, acc in tech["sweep"].items():
        p, r, f = prf(*acc)
        mark = " ← pipeline default" if abs(thr - THRESHOLD) < 1e-9 else ""
        L.append(f"| {thr:.2f} | {fmt(p)} | {fmt(r)} | {fmt(f)}{mark} |")
    L.append("")

    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
