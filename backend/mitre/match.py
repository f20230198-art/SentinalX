"""Match a post to MITRE techniques: verify LLM-suggested T-codes + semantic similarity search."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Iterable

import numpy as np

# Valid technique ID shape: T1234 or T1234.001
T_CODE_RE = re.compile(r"^T\d{4}(?:\.\d{3})?$")


# One technique matched to a post, plus how it was found and why
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
    """Normalise the LLM techniques JSON into a list of {id, name, evidence} dicts."""
    if not techniques_json:
        return []
    try:
        data = json.loads(techniques_json)
    except json.JSONDecodeError:
        return []
    # Reply can be {"techniques": [...]} or just [...]
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
    # Check each LLM-suggested T-code really exists in MITRE
    for c in candidates:
        tcode = normalise_tcode(c.get("id", ""))
        if not tcode or tcode in seen:
            continue
        seen.add(tcode)
        # Exists -> verified; doesn't exist -> unverified (probably made up)
        source = "llm_verified" if tcode in corpus_ids else "llm_unverified"
        evidence = c.get("evidence")
        out.append(Match(tcode, source, None, evidence if isinstance(evidence, str) else None))
    return out


def chunk_text(text: str, max_words: int = 150, overlap: int = 30) -> list[str]:
    """Split text into overlapping ~150-word chunks (MiniLM only reads ~256 tokens)."""
    # Short text -> one chunk
    words = text.split()
    if len(words) <= max_words:
        return [text]
    # Each chunk starts `step` words after the previous one, so chunks overlap a bit
    step = max_words - overlap
    return [
        " ".join(words[i:i + max_words])
        for i in range(0, len(words) - overlap, step)
    ]


def semantic_topk(
    post_vec: np.ndarray,
    corpus_matrix: np.ndarray,
    corpus_ids: list[str],
    topk: int,
    threshold: float,
    exclude: set[str],
) -> list[Match]:
    """Top-k techniques above threshold (best chunk score wins), skipping `exclude`."""
    # No techniques loaded -> nothing to match
    if corpus_matrix.shape[0] == 0:
        return []
    # Similarity of every technique to the post (for chunks, keep each technique's best chunk)
    if post_vec.ndim == 2:
        scores = (corpus_matrix @ post_vec.T).max(axis=1)  # (N, C) -> (N,)
    else:
        scores = corpus_matrix @ post_vec  # (N,)
    # Take more than topk so we can drop excluded ones and still have headroom.
    n_candidates = min(len(scores), topk + len(exclude) + 5)
    # Sort the best candidates, highest score first
    top_idx = np.argpartition(-scores, n_candidates - 1)[:n_candidates]
    top_idx = top_idx[np.argsort(-scores[top_idx])]
    out: list[Match] = []
    # Keep up to topk that pass the threshold and weren't already found by the LLM
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

    # Read every technique's stored vector
    rows = conn.execute(
        "SELECT technique_id, embedding FROM mitre_techniques "
        "WHERE embedding IS NOT NULL ORDER BY technique_id"
    ).fetchall()
    if not rows:
        return [], np.zeros((0, EMBED_DIM), dtype=np.float32), set()
    # Stack them into one matrix so we can score all techniques at once
    ids = [r["technique_id"] for r in rows]
    matrix = np.vstack([from_blob(r["embedding"]) for r in rows])
    return ids, matrix, set(ids)
