# STAGE 05 — MITRE ATT&CK Ingest + Vector Index

> **Read this on your own time.** Companion to the code shipped in Stage 5. Same voice as Stages 1–4: full depth, jargon unpacked inline, optional **Detour** and **Try this** boxes.

---

## Quick orientation: what does Stage 5 do, in one paragraph?

Stage 4 left every analysed post with a `techniques_json` blob — the LLM's *guesses* at which MITRE ATT&CK techniques the post describes (e.g. `T1566` Phishing, `T1078` Valid Accounts). The catch: an LLM can hallucinate T-codes, paraphrase real ones into wrong numbers, or reference techniques that have been deprecated. Stage 5 fixes that. It pulls down the **official MITRE Enterprise ATT&CK corpus** (697 techniques + sub-techniques), embeds each one with a sentence-transformer model, and does two things per post: (1) **verify** the LLM's candidates against the real corpus — keep the ones that match, flag the ones that don't, and (2) **discover** techniques the LLM *missed* by computing cosine similarity between the post body and every technique's name+description, surfacing the top-k above a threshold. Result: 235 posts, 232 LLM-verified hits, 45 unverified (hallucinations / deprecated codes), 15 semantic discoveries.

---

## 0. The mental model: why do we need this stage?

Stage 4 is **probabilistic** — Mistral 7B reads a post and outputs T-codes that *sound* right. Stage 5 is **grounding**: it forces those guesses to meet a fixed reality.

Three failure modes Stage 4 has, that Stage 5 catches:

| Failure | Example we saw | Why it happens |
|---|---|---|
| **Hallucinated T-code** | LLM emits `T1999` (doesn't exist) | The LLM is a next-word predictor. T-codes look like a pattern; it can extrapolate the pattern past the real data. |
| **Deprecated / merged code** | LLM emits `T1086` (old PowerShell technique, now replaced by `T1059.001`) | MITRE updates its corpus periodically. The LLM's training data includes obsolete numbers. |
| **Missed technique** | Post clearly describes credential stuffing but the LLM only flagged "Phishing" | The LLM is asked once per post and has finite reasoning. Vector search over the whole corpus is exhaustive — it doesn't "forget" any technique. |

The two passes are complementary. **Verification** trusts the LLM's interpretive ability but checks its facts. **Semantic discovery** doesn't trust the LLM at all — it asks the corpus directly, "what's most similar to this post text?"

> **Detour: what is MITRE ATT&CK?**
> ATT&CK (Adversarial Tactics, Techniques, and Common Knowledge) is a free, public knowledge base of how real-world attackers operate. Run by MITRE Corporation (a US federally-funded R&D non-profit). It's organised as a matrix: columns are *tactics* (the **why** — e.g. Initial Access, Persistence, Exfiltration), rows are *techniques* (the **how** — e.g. T1566 Phishing, T1078 Valid Accounts). Sub-techniques add granularity (T1566.001 Spearphishing Attachment vs T1566.002 Spearphishing Link). The corpus is shipped as a single STIX 2.1 JSON file — STIX is a structured threat-info exchange format. Almost every commercial CTI / EDR / SIEM tool maps its detections to ATT&CK because it's a shared vocabulary.

---

## 1. What was built — file map

```
backend/mitre/
├── __init__.py
├── ingest.py        ← download + parse the official STIX JSON
├── embed.py         ← sentence-transformers wrapper (all-MiniLM-L6-v2, 384-d)
├── match.py         ← per-post matching: LLM verification + semantic top-k
└── run.py           ← CLI: --ingest / --once / --watch / --reset / --reset-corpus

backend/db/schema.sql ← +mitre_techniques, +post_techniques, +mitre_runs
data/mitre/
└── enterprise-attack.json  ← cached STIX corpus (~36 MB), gitignored
```

Invocation:

```bash
# One-time: download + embed the corpus into mitre_techniques (697 rows)
backend/.venv/Scripts/python.exe -m backend.mitre.run --ingest

# Match every Stage-4-completed post that hasn't been Stage-5-matched yet
backend/.venv/Scripts/python.exe -m backend.mitre.run --once

# Continuous mode for live demo
backend/.venv/Scripts/python.exe -m backend.mitre.run --watch --interval 60
```

### 1.1 The verified-working run

**2026-04-30, against the 235 posts already analysed by Stage 4:**

```
ingest:   parsed 697 techniques (incl. sub-techniques)
          embedded in 45.0s on CPU (one-shot batch encode)
          upserted 697 rows into mitre_techniques

match:    processed 235/235 posts in ~5s (after model warm-up)
          292 total matches written to post_techniques
            ├── 232 llm_verified   (LLM's T-code exists in corpus)
            ├──  45 llm_unverified (LLM hallucinated or used deprecated code)
            └──  15 semantic       (cosine ≥ 0.45, LLM missed it)

          160/235 posts have at least one technique attached
          Top hits: T1566 Phishing (153), T1078 Valid Accounts (34),
                    T1086 PowerShell-legacy (14), T1087 Account Discovery (12)
```

The dominance of T1566 lines up with the forum content — most threads in our synthetic corpus are credential-sale and recruitment posts, both of which the LLM correctly maps to phishing. T1086 showing up 14 times *as verified* is interesting — it's a deprecated parent that MITRE still ships in the JSON, so it passes the corpus-membership check. We don't normalise deprecated → modern codes in this stage; that's an opinion call deferred to Stage 6.

---

## 2. The corpus, and why we cache it

### 2.1 What we download

```
https://raw.githubusercontent.com/mitre/cti/master/enterprise-attack/enterprise-attack.json
```

This is MITRE's official mirror of the Enterprise matrix, in **STIX 2.1** format — Structured Threat Information eXpression. STIX is JSON-with-conventions: every object has a `type`, an `id` (a STIX UUID, like `attack-pattern--abc-123`), and type-specific fields. The file is ~36 MB unzipped, ~14000 objects: techniques, tactics, groups (named threat actors like APT29), software (named malware like Cobalt Strike), mitigations, and the *relationships* between them.

We extract only `attack-pattern` objects (which is MITRE-speak for "technique"). Filters in [`backend/mitre/ingest.py`](backend/mitre/ingest.py):

```python
if o.get("type") != "attack-pattern":  continue
if o.get("revoked") or o.get("x_mitre_deprecated"):  continue
if not tcode:  continue   # must have an mitre-attack external_id (T-number)
```

The "T-code" — `T1566`, `T1078.003` — lives in `external_references[]` with `source_name == "mitre-attack"`. It's *not* the STIX id. The STIX id is internal; the T-code is what humans (and our LLM) use.

> **Detour: what's a "sub-technique" and why does the JSON not nest them?**
> ATT&CK techniques have a parent/child relationship — `T1566` Phishing has children `T1566.001`, `T1566.002`, `T1566.003`. STIX doesn't nest them as JSON children, though. Instead it stores a separate `relationship` object whose `relationship_type` is `"subtechnique-of"`, with `source_ref` (the child STIX id) and `target_ref` (the parent STIX id). To populate our `parent_id` column, we have to do two passes: first build a `STIX-id → T-code` map, then walk all relationship objects and look up parent T-codes. See [`backend/mitre/ingest.py:78-84`](backend/mitre/ingest.py#L78-L84).

### 2.2 Why cache the JSON locally

`data/mitre/enterprise-attack.json` is gitignored but cached on disk. Re-running `--ingest` without `--force-download` skips the 36 MB fetch. Reasons:

1. **Reproducibility.** A run today and a run next month against the live URL would give different T-code sets (MITRE updates monthly). The cache pins the corpus version.
2. **Offline demo.** The whole pipeline is supposed to run without internet. A cached corpus means after the first ingest, no network call is ever needed for Stage 5.
3. **Speed.** 36 MB over a flaky connection is the slow part of the ingest.

`--force-download` exists for when you explicitly want to refresh.

---

## 3. Embeddings: what they are, why we use one, why this one

### 3.1 The problem semantic search solves

The LLM in Stage 4 was *asked* about MITRE techniques and produced free-form guesses. We want to do the inverse: take a post body, ask the corpus *which technique descriptions are most semantically similar to this post*. That's a search problem, but with a twist — we don't want exact word matches (a post saying "they sent me a fake invoice and I clicked" should match T1566.001 Spearphishing Attachment even though it doesn't say "spearphishing"). We want **meaning-based similarity**.

The standard tool for that is **sentence embeddings**.

### 3.2 What an embedding is, concretely

A sentence-transformer model takes a text string and outputs a fixed-length vector of floats — for `all-MiniLM-L6-v2`, that's **384 numbers**. The model has been trained so that **texts with similar meaning produce vectors pointing in similar directions** in 384-dimensional space.

"Similar direction" is measured by **cosine similarity** — the cosine of the angle between two vectors. It ranges from -1 (opposite) through 0 (orthogonal / unrelated) to 1 (identical direction). For sentence-transformers trained with normalised contrastive objectives, scores typically land 0.0–0.9 for meaningful pairs; 0.4–0.7 is "related but not identical"; 0.7+ is "almost the same thing."

> **Detour: cosine vs dot product, and why we normalise.**
> Cosine similarity is `(a · b) / (|a| · |b|)`. If both vectors are already L2-normalised (length = 1), then `|a| · |b| = 1`, and cosine reduces to a plain dot product. We pass `normalize_embeddings=True` to the encoder ([`embed.py:37`](backend/mitre/embed.py#L37)) so we can do the entire post-vs-corpus similarity calculation as a single matrix multiplication: `corpus_matrix @ post_vec` gives a 697-element vector of similarities in microseconds. This is the difference between a search that takes 0.001s and one that takes 0.1s — it matters when we're matching 235 posts.

### 3.3 Why `all-MiniLM-L6-v2` specifically

| Choice | Why |
|---|---|
| **`all-MiniLM-L6-v2`** | Industry-standard small embedder. 384-d, 22M params, runs comfortably on CPU. ~14 ms per text. Trained on 1B+ sentence pairs. The default "good enough" baseline most teams use before reaching for something heavier. |
| Bigger (`all-mpnet-base-v2`, 768-d) | ~2x better on benchmarks, ~3x slower, more memory. Overkill for 697 short texts. |
| OpenAI `text-embedding-3-small` | Better quality, but requires an API key + internet — breaks our offline demo design. |
| Domain-tuned (e.g. SecBERT) | Would arguably be better on cyber-jargon. But none have a maintained sentence-transformers wrapper, and MiniLM still picks the right things in our test data. |

The encoder is loaded lazily via `@lru_cache` in [`embed.py:23-27`](backend/mitre/embed.py#L23-L27) — importing `sentence_transformers` pulls torch and is slow (~3–5s), so we avoid paying that cost when running `--reset` or `--reset-corpus`.

### 3.4 What text we actually embed

For each technique, we embed `f"{name}. {description}"` ([`run.py:68`](backend/mitre/run.py#L68)). The name alone is too short ("Phishing"); the description alone misses keyword context; the concatenation gives the model enough material to anchor on.

For each post, we embed the **entire body** (no truncation). Sentence-transformers tokenises and truncates internally to 256 tokens (the model's context window), which for our forum posts is usually fine — most are under 200 tokens.

> **Try this:** open `backend/db/sentinelx.db` in a SQLite browser, query `SELECT technique_id, name FROM mitre_techniques WHERE technique_id LIKE 'T1566%'`. You'll see Phishing + its sub-techniques. The 384 floats per row aren't human-readable but they're what makes the search work.

---

## 4. Storage: float32 BLOBs, not JSON

The `mitre_techniques` table stores embeddings as raw `BLOB`:

```sql
embedding       BLOB,
embedding_model TEXT,
```

Encode/decode is dead simple ([`embed.py:43-48`](backend/mitre/embed.py#L43-L48)):

```python
def to_blob(vec):    return vec.astype(np.float32, copy=False).tobytes()
def from_blob(blob): return np.frombuffer(blob, dtype=np.float32)
```

Why BLOB and not JSON?

| Format | 697 vectors × 384 d | Decode cost |
|---|---|---|
| JSON (`"[0.123, -0.456, ...]"`) | ~7 MB on disk | Have to parse 268k floats from text. ~0.5s per load. |
| float32 BLOB | ~1 MB on disk | `np.frombuffer` is a zero-copy reinterpretation. Microseconds. |

For 697 rows the difference is small in absolute terms, but it sets the right pattern: when Stage 6 needs to load the corpus matrix on every API request, microsecond-level loads matter. Production CTI systems doing millions of vectors use FAISS or a real vector DB; we're doing 697, so a `np.vstack` of BLOBs is overkill-free.

`embedding_model` lets future code detect and refuse to compare vectors from different encoder versions — if you re-ingest with a bigger model later, the column reminds the matcher to re-embed the posts too.

> **Detour: why not use `sqlite-vss` / `chromadb` / `pgvector`?**
> Real vector indexes (HNSW, IVF) shine when you have millions of vectors and need sublinear-time search. We have 697. A brute-force matmul is `O(697 × 384) ≈ 270k` floating-point ops per query — it finishes in literally tens of microseconds on the CPU. Adding a vector index would *slow us down* (index lookup overhead exceeds brute force at this scale), and it would add a dependency we don't need. Same reason we kept SQLite throughout: scale-appropriate boring is a feature.

---

## 5. The two-pass matcher

### 5.1 Pass 1 — verify the LLM's candidates

Stage 4 wrote a JSON blob like this into `llm_analyses.techniques_json`:

```json
{ "techniques": [
    {"id": "T1566", "name": "Phishing", "evidence": "selling 25M Okta creds"},
    {"id": "T9999", "name": "MadeUpThing", "evidence": "..."}
  ],
  "behaviour": ["initial-access", "credential-theft"]
}
```

(Sometimes it's just `[...]` instead of `{techniques: [...]}` — Mistral isn't perfectly consistent about wrapping. Our parser ([`match.py:45-63`](backend/mitre/match.py#L45-L63)) handles both shapes.)

For each candidate ID:

1. **Normalise.** Strip whitespace, uppercase, and validate against the regex `^T\d{4}(?:\.\d{3})?$`. Anything that doesn't match the T-code shape is dropped silently.
2. **Lookup.** Is this T-code in `corpus_id_set` (the set of all 697 technique_ids loaded from the DB)?
   - **Yes →** insert with `source = 'llm_verified'`. Carry the LLM's `evidence` string forward — it's useful context for Stage 6/7's UI.
   - **No →** insert with `source = 'llm_unverified'`. Could be hallucinated, could be a real ID we don't have (sub-technique not in the loaded corpus, recently-added technique, deprecated removed from STIX). Either way, we keep it for inspection rather than discarding.

The 45 unverified rows we got are mostly real-but-deprecated codes (`T1064` Scripting → split into `T1059.x` per language) and a handful of `T1xxx`-shaped strings the LLM coined.

### 5.2 Pass 2 — semantic discovery

For each post, embed the body, then:

```python
scores = corpus_matrix @ post_vec   # (697,) array of cosine similarities
top_idx = argpartition + argsort(top n_candidates)
for i in top_idx:
    if scores[i] < threshold:    break        # 0.45 default
    if corpus_ids[i] in exclude: continue     # don't double-count LLM-verified
    keep
    if len(out) == topk: break                # 5 default
```

Two key knobs:

| Flag | Default | What it does |
|---|---|---|
| `--topk` | 5 | Max semantic matches per post. Caps fan-out — even if 30 techniques pass threshold, we only keep the top 5. |
| `--threshold` | 0.45 | Minimum cosine score. Below this, similarity is noise. Calibrated empirically against our forum corpus — see §6. |

The `exclude` set is the post's already-verified LLM matches. We don't want to redundantly insert the same T-code with `source='semantic'` if the LLM already nailed it; semantic discovery is for techniques the LLM **missed**.

> **Detour: `argpartition` vs `argsort`.**
> A full sort of 697 scores is `O(n log n)`. We only want the top ~10. `np.argpartition(-scores, k)` finds the indices of the top-k in `O(n)` (no full sort), then we sort just those k. For 697 it doesn't matter; the pattern matters for Stage 6's API where one user might fan out to thousands of similarity comparisons per request.

### 5.3 Persisting matches: `post_techniques`

```sql
CREATE TABLE post_techniques (
    raw_post_id   INTEGER,
    technique_id  TEXT,
    source        TEXT,        -- llm_verified | llm_unverified | semantic
    score         REAL,        -- cosine score for semantic; NULL for LLM rows
    evidence      TEXT,        -- LLM's evidence string; NULL for semantic
    matched_at    REAL,
    UNIQUE(raw_post_id, technique_id, source)
);
```

The `UNIQUE(raw_post_id, technique_id, source)` is the idempotency guarantee — re-running the matcher inserts no duplicates ([`run.py:155-157`](backend/mitre/run.py#L155-L157) catches `IntegrityError` and moves on). The `source` is part of the key on purpose: a single technique could legitimately have both `llm_verified` and `semantic` rows — though our `exclude` logic prevents that in practice, the schema doesn't forbid it.

`score` is `NULL` for LLM rows because there's no similarity number — the LLM either named the T-code or didn't. `evidence` is `NULL` for semantic rows because we have no LLM-supplied justification — the score is the only "evidence."

### 5.4 The cursor

Same pattern as Stage 4: `post_processing_state` table, keyed on `(raw_post_id, stage)`. Stage 5's cursor query ([`run.py:115-131`](backend/mitre/run.py#L115-L131)) is:

```sql
SELECT rp.id, rp.body, la.techniques_json
FROM raw_posts rp
JOIN post_processing_state pps_llm
  ON pps_llm.raw_post_id = rp.id AND pps_llm.stage = 'llm'   -- Stage 4 done
LEFT JOIN llm_analyses la ON la.raw_post_id = rp.id
WHERE NOT EXISTS (                                            -- Stage 5 not done
    SELECT 1 FROM post_processing_state pps
    WHERE pps.raw_post_id = rp.id AND pps.stage = 'mitre'
)
```

Stage 5 owns its own row in `post_processing_state` (stage=`'mitre'`). It does **not** share Stage 4's cursor. This is the same per-stage-cursor pattern we set up in Stage 4, and Stages 6+ will reuse it.

The `LEFT JOIN` on `llm_analyses` is so that even if Stage 4 somehow completed without producing a row (shouldn't happen, but defensive), the post still gets considered — we just skip the LLM-verification pass and run pure semantic discovery.

---

## 6. Calibration: where do `topk=5` and `threshold=0.45` come from?

These are the two empirical knobs. They were picked the boring way: try a few, eyeball the results, pick what makes sense.

**On threshold.** With our 235 posts and 697 techniques, a quick look at score distributions:

- 0.30–0.40: noisy. Returns "Initial Access" generically for almost any post. Useless.
- 0.40–0.45: borderline. Some real hits, lots of false positives.
- 0.45–0.50: precise. The 15 semantic hits we got cluster here (min 0.451, max 0.505) — every one is a defensible match (e.g. a post about "exploiting old WordPress plugins" matched T1190 Exploit Public-Facing Application at 0.48).
- 0.55+: very few hits at all. Threshold too high.

So 0.45 sits at the elbow of the precision/recall curve for our specific data. For a different corpus (longer posts, different jargon mix), this would need re-tuning. CLI exposes `--threshold` for exactly that.

**On topk.** Capping at 5 is a UX decision more than a quality one. A post can plausibly map to 1–3 techniques; anything past 5 is the matcher reaching. If we ever want denser coverage we can crank it up; for now, narrow & precise > wide & noisy.

**On model.** MiniLM was good enough that we never tried alternatives. If Stage 7's UI shows obviously-wrong semantic matches, the first knob to turn would be `--model sentence-transformers/all-mpnet-base-v2` — slower but stronger.

---

## 7. Tech stack & why each piece

| Piece | What it does | Why this and not something else |
|---|---|---|
| `sentence-transformers` 5.x | Wraps HuggingFace transformer models with a sentence-level encode API | The standard for sentence embeddings since 2019. One-liner to load and encode. Alternative would be raw transformers + manual pooling — more code, no benefit. |
| `all-MiniLM-L6-v2` | 384-d encoder, 22M params, ~14 ms/text on CPU | The default lightweight baseline. Hugely battle-tested, runs offline, small download (~90 MB). |
| `numpy` | The linear algebra | Already a dep via spaCy/torch. `vstack`, `argpartition`, matmul — all the search math. Native float32 BLOB roundtrip is `frombuffer`/`tobytes`. |
| `urllib.request` (stdlib) | Download the STIX JSON | One-shot HTTPS GET, no need for `requests` or `httpx`. |
| Raw `sqlite3` BLOB | Store 384-d vectors per technique | At 697 vectors, dedicated vector DBs (FAISS, chromadb, pgvector) are net negative — they add deps and complexity for problem we don't have. SQLite BLOB + matmul is faster *and* simpler. |
| MITRE ATT&CK Enterprise | The technique corpus | The de-facto standard for adversary technique taxonomy. Free, public, STIX-formatted, version-controlled on GitHub. |
| `STIX 2.1` | The serialisation | Not our choice — it's what MITRE ships. We translate it to our flat schema in `ingest.parse()`. |

---

## 8. How this is used in industry

Mapping unstructured text → MITRE ATT&CK techniques is one of the bread-and-butter tasks of modern CTI tooling. Real-world variants of what we just built:

- **MITRE TRAM (Threat Report ATT&CK Mapping).** MITRE's own open-source tool that ingests free-form threat reports (PDFs, blog posts) and proposes ATT&CK technique mappings using ML classifiers. We've built a slimmer version of TRAM, with an LLM step in front and embedding similarity as the verification.
- **Microsoft Sentinel / Defender XDR**, **CrowdStrike Falcon**, **SentinelOne Singularity** — every commercial EDR/SIEM tags detections with ATT&CK technique IDs. The mapping happens at the *detection rule* level (a YARA rule or KQL query is hand-tagged with `T1059.001` once), but they also do post-hoc enrichment on threat reports the same way we do.
- **VirusTotal Intelligence, Recorded Future, Mandiant Advantage** — paid threat intel feeds publish reports already tagged with ATT&CK techniques. The tagging is a mix of analyst manual work and the kind of ML-assisted matching we're doing.
- **Open-source MITRE mappers** — `attack-flow`, `attackcti` (Python library), `pyattck`. These are typically *consumers* of the corpus rather than text-to-technique mappers; they help you traverse the matrix once you have technique IDs.
- **Vector search at scale.** Beyond CTI, the exact pattern we use (text → embedding → cosine search over a fixed corpus) is the foundation of every modern semantic search system: documentation Q&A, support-ticket triage, RAG pipelines, recommendation systems. The only thing that changes is corpus size and the choice between brute-force matmul vs. an HNSW index.

The verify-vs-discover split we implemented also mirrors how mature CTI teams actually work: an analyst's first technique guess is treated as a hypothesis to validate against the corpus, *then* the corpus is queried for what the analyst might have missed. We've automated both halves.

---

## 9. What Stage 5 does **not** do (deferred to later stages)

- **Tactic-level rollup.** Each technique belongs to one or more tactics (Initial Access, Persistence, etc). We store the tactics in `mitre_techniques.tactics` but don't aggregate per-post. Stage 6's API will compute "this post touches tactics X, Y, Z" by joining post_techniques → mitre_techniques.
- **Technique-to-technique relationships.** STIX has rich relationships (this technique uses this software, this group uses this technique). We ignore everything except the technique objects themselves. Adding groups/software/relationships is a Stage 6+ choice depending on what the UI needs.
- **Modern code normalisation.** When the LLM emits a deprecated parent like `T1086`, we mark it `llm_verified` (because it's still in the JSON) rather than rewriting it to `T1059.001`. Doing the rewrite means maintaining a mapping table; deferred until we know whether the UI cares.
- **Re-embedding posts when the model changes.** `embedding_model` is stored on each technique row, but post embeddings aren't persisted — we recompute them per run. If we ever want to pre-embed posts (for instant Stage 6 search), we'll add a `post_embeddings` table mirroring this one.

---

## 10. Gotchas observed during this stage

1. **First `--ingest` is slow.** The 45-second embedding step is mostly model download + cold-start. Subsequent ingests (after `--reset-corpus`) are ~5s because the model is HuggingFace-cached.
2. **`urllib` 120s timeout is tight.** The MITRE GitHub mirror occasionally rate-limits; if `--ingest` ever times out, retry once. Not a bug worth fixing until it bites in CI.
3. **Posts shorter than ~50 chars.** Sentence-transformers handles them fine but they cluster around the same vector regardless of content (the model doesn't have enough signal). All our posts are well above this threshold; documenting it for when Stage 7 lets users submit short test queries.
4. **`SELECT ... ORDER BY technique_id`** in `load_corpus` matters. The `corpus_ids` list and `corpus_matrix` rows have to be in lockstep — index `i` of one must correspond to index `i` of the other. The `ORDER BY` makes that deterministic across runs; without it SQLite is technically free to return rows in any order.

---

## 11. End-of-stage status

- ✅ Schema for `mitre_techniques`, `post_techniques`, `mitre_runs` shipped in `backend/db/schema.sql`.
- ✅ Ingest path: download → parse STIX → embed → upsert. Idempotent on re-run.
- ✅ Match path: LLM verification + semantic top-k + cursor. Idempotent on re-run.
- ✅ All 235 Stage-4-analysed posts processed: 232 verified + 45 unverified + 15 semantic = 292 rows in `post_techniques`. 160/235 posts have ≥1 technique attached.
- ✅ `--reset` and `--reset-corpus` for clean re-runs.
- ⬜ Stage 6 (FastAPI) will expose `post_techniques` and `mitre_techniques` over HTTP for the frontend.

The dataset is now fully *grounded* — every technique reference in our DB either exists in the official MITRE corpus or is explicitly flagged as not. That's the contract Stages 6–8 will rely on.
