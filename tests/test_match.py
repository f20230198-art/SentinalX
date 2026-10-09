"""MITRE matching: T-code parsing, LLM verification, semantic top-k, chunking."""

import json

import numpy as np

from backend.mitre.match import (
    chunk_text,
    normalise_tcode,
    parse_llm_candidates,
    semantic_topk,
    verify_llm,
)


def test_normalise_tcode():
    assert normalise_tcode(" t1566.001 ") == "T1566.001"
    assert normalise_tcode("T15") is None
    assert normalise_tcode("Phishing") is None
    assert normalise_tcode(None) is None


def test_parse_candidates_accepts_all_llm_shapes():
    assert parse_llm_candidates(json.dumps({"techniques": [{"id": "T1566"}]})) == [{"id": "T1566"}]
    assert parse_llm_candidates(json.dumps(["T1486"])) == [{"id": "T1486"}]
    assert parse_llm_candidates("not json") == []
    assert parse_llm_candidates(None) == []


def test_verify_splits_real_and_hallucinated_codes():
    corpus = {"T1566", "T1486"}
    out = verify_llm([{"id": "T1566", "evidence": "phish kit"}, {"id": "T9999"}, {"id": "t1566"}], corpus)
    assert [(m.technique_id, m.source) for m in out] == [
        ("T1566", "llm_verified"),
        ("T9999", "llm_unverified"),   # hallucinated id
    ]                                  # duplicate t1566 collapsed
    assert out[0].evidence == "phish kit"


# Helper: scale a vector to length 1
def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_semantic_topk_threshold_order_and_exclude():
    corpus = np.stack([_unit([1, 0, 0]), _unit([0.9, 0.1, 0]), _unit([0, 1, 0])])
    ids = ["T1", "T2", "T3"]
    post = _unit([1, 0, 0])
    out = semantic_topk(post, corpus, ids, topk=5, threshold=0.5, exclude=set())
    assert [m.technique_id for m in out] == ["T1", "T2"]       # T3 below threshold
    assert out[0].score > out[1].score
    out = semantic_topk(post, corpus, ids, topk=5, threshold=0.5, exclude={"T1"})
    assert [m.technique_id for m in out] == ["T2"]             # already LLM-verified


def test_semantic_topk_max_pools_over_chunks():
    corpus = np.stack([_unit([1, 0]), _unit([0, 1])])
    # Chunk 1 matches T1, chunk 2 matches T2: both should surface.
    chunks = np.stack([_unit([1, 0]), _unit([0, 1])])
    out = semantic_topk(chunks, corpus, ["T1", "T2"], topk=5, threshold=0.9, exclude=set())
    assert {m.technique_id for m in out} == {"T1", "T2"}


def test_chunk_text_short_post_is_one_chunk():
    assert chunk_text("a b c") == ["a b c"]


def test_chunk_text_covers_every_word_with_overlap():
    words = [f"w{i}" for i in range(400)]
    chunks = chunk_text(" ".join(words), max_words=150, overlap=30)
    assert len(chunks) > 1
    assert all(len(c.split()) <= 150 for c in chunks)
    seen = {w for c in chunks for w in c.split()}
    assert seen == set(words)
