"""Match a post against the MITRE ATT&CK corpus.

Two paths per post:

1. Verify LLM candidates: the techniques_json column from Stage 4 is a list of
   {id, name, evidence} dicts. For each, check whether the id exists in the
   corpus -- 'llm_verified' if yes, 'llm_unverified' if no (LLM hallucinated
   or referenced a deprecated/sub-technique not in the loaded corpus).

2. Semantic discovery: embed the post body, compute cosine similarity against
   every technique vector, take the top-k whose score >= threshold. Skip any
   technique already covered by the LLM-verified set (no double-counting).
   Source = 'semantic'.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Iterable

import numpy as np

T_CODE_RE = re.compile(r"^T\d{4}(?:\.\d{3})?$")


@dataclass(frozen=True)
class Match:
    technique_id: str
    source: str   # 'llm_verified' | 'llm_unverified' | 'semantic'
    score: float | None
    evidence: str | None


def normalise_tcode(raw: str) -> str | None:
    """Strip surrounding whitespace and validate the T-code shape."""
    if not isinstance(raw, str):
        return None
    s = raw.strip().upper()
    return s if T_CODE_RE.match(s) else None


def parse_llm_candidates(techniques_json: str | None) -> list[dict]:
    """Stage 4's techniques_json holds {techniques: [...], behaviour: [...]} or
    sometimes just [...]. We normalise to a list of dicts with at least id +
    optional name/evidence.
    """
    if not techniques_json:
        return []
    try:
        data = json.loads(techniques_json)
    except json.JSONDecodeError:
        return []
    items = data.get("techniques", []) if isinstance(data, dict) else data
    out: list[dict] = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and "id" in it:
            out.append(it)
        elif isinstance(it, str):
            out.append({"id": it})
    return out


def verify_llm(candidates: list[dict], corpus_ids: set[str]) -> list[Match]:
    out: list[Match] = []
    seen: set[str] = set()
    for c in candidates:
        tcode = normalise_tcode(c.get("id", ""))
        if not tcode or tcode in seen:
            continue
        seen.add(tcode)
        source = "llm_verified" if tcode in corpus_ids else "llm_unverified"
        evidence = c.get("evidence")
        out.append(Match(tcode, source, None, evidence if isinstance(evidence, str) else None))
    return out


def semantic_topk(
    post_vec: np.ndarray,
    corpus_matrix: np.ndarray,
    corpus_ids: list[str],
    topk: int,
    threshold: float,
    exclude: set[str],
) -> list[Match]:
    """Return up to topk Matches whose cosine score >= threshold and whose
    technique_id is not in `exclude`. Vectors are assumed L2-normalised so
    cosine = dot product.
    """
    if corpus_matrix.shape[0] == 0:
        return []
    scores = corpus_matrix @ post_vec  # (N,)
    # Take more than topk so we can drop excluded ones and still have headroom.
    n_candidates = min(len(scores), topk + len(exclude) + 5)
    top_idx = np.argpartition(-scores, n_candidates - 1)[:n_candidates]
    top_idx = top_idx[np.argsort(-scores[top_idx])]
    out: list[Match] = []
    for i in top_idx:
        if len(out) >= topk:
            break
        s = float(scores[i])
        if s < threshold:
            break
        tid = corpus_ids[i]
        if tid in exclude:
            continue
        out.append(Match(tid, "semantic", s, None))
    return out


def load_corpus(conn: sqlite3.Connection) -> tuple[list[str], np.ndarray, set[str]]:
    """Load the embedding matrix from mitre_techniques. Returns (ids, matrix, id_set)."""
    from backend.mitre.embed import from_blob, EMBED_DIM

    rows = conn.execute(
        "SELECT technique_id, embedding FROM mitre_techniques "
        "WHERE embedding IS NOT NULL ORDER BY technique_id"
    ).fetchall()
    if not rows:
        return [], np.zeros((0, EMBED_DIM), dtype=np.float32), set()
    ids = [r["technique_id"] for r in rows]
    matrix = np.vstack([from_blob(r["embedding"]) for r in rows])
    return ids, matrix, set(ids)
