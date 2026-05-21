# STAGE 05.5 — MITRE Mitigations (Defensive Recommendations)

> **Read this on your own time.** Companion to the code shipped in Stage 5.5. Same voice as Stages 1–8: full depth, jargon unpacked inline, optional **Detour** and **Try this** boxes.

---

## Quick orientation: what does Stage 5.5 do, in one paragraph?

Stage 5 left every post mapped to MITRE ATT&CK *techniques* — the **attacks**. A report that ends there describes the threat and stops; an analyst reading it immediately asks "so what do I *do* about it?" Stage 5.5 answers that. The same Enterprise ATT&CK STIX file we already parse for techniques *also* contains MITRE's official **countermeasures** — `course-of-action` objects, coded `Mxxxx`, each linked to the techniques it defends against. Stage 5.5 parses those, stores them in two new tables, and surfaces them everywhere a technique already appears: a "Defensive Recommendations" card on each post, a coverage-ranked "Priority Mitigations" list on each investigation, and a "Recommended Actions" section in the exported PDF. Zero LLM, zero network, zero hallucination — it is a pure lookup over data already on disk. Result: 44 mitigations, 1448 technique→mitigation links, 24 of the 35 distinct techniques on the seeded corpus carry at least one recommendation.

---

## 0. The mental model: why does a recon tool need this stage?

SentinelX up to Stage 5 is a **recon** tool — it finds and characterises threats. Stage 5.5 does not change that identity; it *completes the report*.

The distinction is worth being precise about, because "give solutions to vulnerabilities" can drift into a different product entirely:

| | |
|---|---|
| ❌ **Out of scope** | "Scan this server, find its CVEs, generate custom patches." That is a *vulnerability scanner* — a different tool, and one that would invent fixes. |
| ✅ **Stage 5.5** | "This post already maps to technique T1133. Here is MITRE's *own published* defense for T1133." Still 100% about the darknet post — just a more complete answer. |

The whole feature rests on one fact: **MITRE does not only publish attacks.** For most techniques it also publishes the official countermeasures. A threat report that says "here is the danger" but not "here is the fix" is considered half-finished in the industry — every commercial CTI platform (Recorded Future, Mandiant) closes that loop. Stage 5.5 closes it for SentinelX, and closes it *honestly*: every recommendation traces to a MITRE `course-of-action` object, never to a language model.

> **Detour: what is a MITRE "mitigation"?**
> Alongside the technique matrix, ATT&CK ships a set of **mitigations** — defensive measures, each with an `Mxxxx` code (e.g. `M1032` Multi-factor Authentication, `M1030` Network Segmentation, `M1017` User Training). There are 44 of them for Enterprise. They are deliberately coarse: a mitigation is a *class* of defense ("do MFA"), not a product or a config snippet. MITRE connects them to techniques with relationship objects — one mitigation typically defends many techniques, and one technique is typically defended by several mitigations. It is a many-to-many graph. Some techniques (notably discovery techniques like "Account Discovery") have **no** mitigation at all — MITRE's position is that you cannot meaningfully prevent an attacker from *looking*, only detect it. That is not a gap in our data; it is MITRE's considered stance, and Stage 5.5 surfaces it faithfully.

---

## 1. What was built — file map

```
backend/db/schema.sql        ← +mitre_mitigations, +technique_mitigations
backend/mitre/
├── ingest.py                ← +Mitigation, +MitigationLink, +parse_mitigations()
└── run.py                   ← +ingest_mitigations(), +--ingest-mitigations flag
backend/api/
├── main.py                  ← +_mitigations_for_techniques(); /posts/{id} now returns mitigations[]
├── investigations.py        ← +aggregate_mitigations(); /investigations/{id} returns ranked mitigations[]
└── export.py                ← +_build_mitigations_html(); "Recommended actions" PDF section
frontend/src/
├── lib/api.ts               ← +PostMitigation, +InvestigationMitigation types
├── components/DetailPanel.tsx← "Defensive Recommendations" card + MitigationItem
└── pages/Investigations.tsx ← "Priority Mitigations" list + MitigationRow
```

Invocation — one new command, offline and ~1 second:

```bash
# Parse + upsert mitigations from the already-cached STIX corpus.
backend/.venv/Scripts/python.exe -m backend.mitre.run --ingest-mitigations
```

`--ingest` also runs it automatically, so a full corpus ingest populates everything in one go.

### 1.1 The verified-working run

**Against the cached `data/mitre/enterprise-attack.json` and the 236-post demo DB:**

```
ingest-mitigations: parsed 44 mitigations, 1448 technique links
                    upserted 44 mitigations, 1448 links (0 skipped — every
                      link's technique end exists in mitre_techniques)

coverage:           24 of 35 distinct techniques on the corpus have >=1 mitigation
                      (the other 11 are techniques MITRE publishes no countermeasure for)

per-post sample:    post #50 -> 24 deduped mitigations across its techniques
investigation:      inv #3 (236 matched posts) -> 33 ranked mitigations
                      top: M1017 User Training — addresses 133 of 236 posts (66%)
PDF:                /investigations/3/export -> 200, valid PDF, "Recommended actions"
                      table rendered
```

The dominance of *User Training*, *Audit*, and *Antivirus/Antimalware* at the top of the investigation ranking lines up with the corpus content — most threads are phishing-adjacent credential and fraud posts, and those three are the broadest countermeasures MITRE lists against that family. That is the feature working as designed: it tells the analyst *what to do first*.

---

## 2. The data source — already on disk

### 2.1 Three STIX object types, one file

Stage 5 downloaded `enterprise-attack.json` and parsed only `attack-pattern` objects. That same ~36 MB file contains everything Stage 5.5 needs — **no new download, no network call**:

| STIX `type` | Stage 5.5 uses it for | Code |
|---|---|---|
| `course-of-action` | The mitigations themselves (Mxxxx, name, description, URL) | `parse_mitigations()` |
| `relationship` (type `mitigates`) | The edges: which mitigation defends which technique | `parse_mitigations()` |
| `attack-pattern` | Re-read, only to know which T-codes are valid (for FK safety) | `parse_mitigations()` |

A `mitigates` relationship has `source_ref` = the course-of-action's STIX id and `target_ref` = the technique's STIX id. As with sub-techniques in Stage 5, **STIX never nests** — the link is a separate object you must resolve by id.

> **Detour: STIX ids vs. external ids, again.**
> Every STIX object has an internal `id` like `course-of-action--9bb9e696-...`. The human-facing code (`M1032`, `T1566`) lives in `external_references[]` under `source_name == "mitre-attack"` — exactly the same place the T-code lives, so we reuse Stage 5's existing `_external_id_and_url()` helper unchanged. The `mitigates` relationship references the *STIX ids*; to key our join table by `Mxxxx`/`Txxxx` we build two `STIX-id → code` maps and translate both ends. This is the identical two-pass shape Stage 5 used for `subtechnique-of`.

### 2.2 Why re-parse instead of re-download

`parse_mitigations()` reads `DEFAULT_CACHE` — the file Stage 5 already cached. The same three reasons from Stage 5 apply: **reproducibility** (the corpus version is pinned), **offline demo** (no network after the first ingest), **speed** (parsing 36 MB of JSON is ~1s; downloading it is the slow part). There is no `--force-download` on `--ingest-mitigations` because it never downloads at all — if you want a fresh corpus you run `--ingest`, which refreshes the file and then calls `ingest_mitigations()` itself.

---

## 3. Schema: two tables, the established patterns

```sql
CREATE TABLE IF NOT EXISTS mitre_mitigations (
    mitigation_id   TEXT    PRIMARY KEY,         -- Mxxxx
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    url             TEXT,
    ingested_at     REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS technique_mitigations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    technique_id    TEXT    NOT NULL,
    mitigation_id   TEXT    NOT NULL,
    ingested_at     REAL    NOT NULL,
    UNIQUE(technique_id, mitigation_id),
    FOREIGN KEY(technique_id)  REFERENCES mitre_techniques(technique_id)   ON DELETE CASCADE,
    FOREIGN KEY(mitigation_id) REFERENCES mitre_mitigations(mitigation_id) ON DELETE CASCADE
);
```

Every design choice here is a deliberate copy of a pattern already in the codebase:

- **`mitre_mitigations` mirrors `mitre_techniques`** — code as `PRIMARY KEY`, an `ingested_at` audit timestamp. The one difference: no `embedding` BLOB. Mitigations are never searched semantically; they are *looked up* from a technique you already have. No vectors needed.
- **`technique_mitigations` mirrors `post_techniques`** — a pure join table with `UNIQUE(technique_id, mitigation_id)`. That UNIQUE constraint is the idempotency guarantee: re-running `--ingest-mitigations` inserts no duplicates (the upsert's `ON CONFLICT` clause handles it — see §4).
- **`ON DELETE CASCADE` both ways** — consistent with the rest of `schema.sql`. If a technique row is ever wiped (`--reset-corpus`), its mitigation links go with it; no orphans.
- **`CREATE TABLE IF NOT EXISTS`** — the table simply appears the next time `Store()` opens the DB, via the existing `_init_schema()`. **There is no migration script.** Your existing 236 posts and 295 `post_techniques` rows are never touched — every change in Stage 5.5 is purely additive.

> **Detour: why a join table instead of a JSON column on `mitre_techniques`?**
> We could have stored `mitigations: ["M1032","M1030"]` as a JSON blob on each technique row. We didn't, for the same reason `post_techniques` is a table and not a JSON field on `raw_posts`: the relationship is queried *from both directions*. The post view asks "given these techniques, what mitigations?" The investigation view asks "given these posts' techniques, which mitigation covers the most posts?" — a `GROUP BY` and `COUNT`. A join table makes both a plain SQL query; a JSON column would force application-side aggregation. Relational data wants a relational shape.

---

## 4. Ingest: parse, then idempotent upsert

### 4.1 `parse_mitigations()` — three filtered passes

[`backend/mitre/ingest.py`](../../backend/mitre/ingest.py) gains two frozen dataclasses (`Mitigation`, `MitigationLink`) and one function. The function does three passes over `objects`, each with the **same filters Stage 5 uses** — drop `revoked`/`x_mitre_deprecated`, require an `mitre-attack` external id:

1. **Pass 1 — mitigations.** Walk `course-of-action` objects; build `coa_stix_to_mid` (STIX id → Mxxxx) and the `Mitigation` list.
2. **Pass 2 — valid techniques.** Walk `attack-pattern` objects; build `tech_stix_to_tcode` and a `valid_tcodes` set. This pass exists purely so Pass 3 can guarantee the foreign key holds.
3. **Pass 3 — links.** Walk `relationship` objects where `relationship_type == "mitigates"`; translate both `source_ref` and `target_ref` to codes; **drop any link whose technique end is not in `valid_tcodes`**; dedupe `(tcode, mid)` pairs.

That last filter is the defensive bit. A `mitigates` relationship could in principle point at a technique that was revoked (and so excluded from our parse). Letting that link through would later violate the `FOREIGN KEY` into `mitre_techniques`. Filtering at parse time means the data is clean before it ever reaches SQL — the same "be strict at the boundary" instinct as Stage 5's `verify_llm`.

### 4.2 `ingest_mitigations()` — upsert with `ON CONFLICT`

[`backend/mitre/run.py`](../../backend/mitre/run.py) gains `ingest_mitigations()`, which writes both tables with the exact upsert idiom Stage 5's `ingest()` already uses for `mitre_techniques`:

```python
INSERT INTO mitre_mitigations (...) VALUES (...)
ON CONFLICT(mitigation_id) DO UPDATE SET name = excluded.name, ...
```

`ON CONFLICT ... DO UPDATE` is **upsert** — insert if new, update in place if the key already exists. Re-running `--ingest-mitigations` after MITRE publishes a revised corpus quietly refreshes every row instead of erroring on the `PRIMARY KEY` / `UNIQUE` collision. The function is therefore safe to run any number of times.

It also re-checks the foreign key a *second* time at insert — `known = {... SELECT technique_id FROM mitre_techniques}` — and counts `skipped`. `parse_mitigations()` already filtered against the corpus, so in a healthy run `skipped` is 0 (and our verified run confirms it). But if you somehow run `--ingest-mitigations` before `--ingest` — empty `mitre_techniques` — this catch logs a clear warning instead of letting the DB throw an `IntegrityError` mid-loop. Belt and suspenders, cheap to keep.

> **Detour: why a separate `--ingest-mitigations` flag at all?**
> `--ingest` already calls `ingest_mitigations()`. So why expose the standalone flag? Because `--ingest` is *slow* — it loads `sentence-transformers`, pulls torch, and re-embeds all 697 techniques (~45s, §1.1 of Stage 5). Mitigation parsing needs **none** of that — no model, no vectors, ~1s. The standalone flag lets you re-pull mitigations independently and instantly: useful when MITRE updates the corpus, or when iterating on `parse_mitigations()` itself. Fast feedback loops are worth one extra `add_argument`.

### 4.3 `--reset-corpus` extended

`reset_corpus()` now wipes `technique_mitigations` and `mitre_mitigations` alongside `mitre_techniques`, in FK-safe order (children before parents). Without this, a `--reset-corpus` followed by `--ingest` would leave stale mitigation rows. Small change, keeps the reset honest.

---

## 5. The API: lookup downstream, aggregate downstream

Two helpers, two shapes. Both are **pure SQL lookups** — no LLM, no embeddings, no I/O beyond SQLite.

### 5.1 Per-post — `_mitigations_for_techniques()`

In [`backend/api/main.py`](../../backend/api/main.py), `GET /posts/{id}` already resolves a post's `techniques[]`. The new helper takes that list of T-codes and resolves it to mitigations:

```sql
SELECT m.mitigation_id, m.name, m.description, m.url, tm.technique_id
FROM technique_mitigations tm
JOIN mitre_mitigations m ON m.mitigation_id = tm.mitigation_id
WHERE tm.technique_id IN (?, ?, ...)
```

Because one mitigation defends many techniques, a post mapped to several techniques will see the *same* mitigation returned several times. The helper **dedupes by `mitigation_id`** into a dict, and on each entry accumulates an `addresses` list — *which* of the post's T-codes this mitigation counters. The UI uses `addresses` to show the analyst *why* a recommendation is here ("M1032 — counters T1133, T1078"). Each entry also gets a `coverage` count, and the list is sorted `coverage` descending so the most broadly-applicable defense is first.

The response gains one key: `"mitigations": [...]`. Existing consumers ignoring it are unaffected — additive, again.

### 5.2 Per-investigation — `aggregate_mitigations()`

The investigation view is the more interesting one, because it answers a *prioritisation* question. In [`backend/api/investigations.py`](../../backend/api/investigations.py), `get_investigation()` already gathers `matched_posts`. The new helper takes those post ids and runs:

```sql
SELECT m.mitigation_id, m.name, ..., pt.raw_post_id, tm.technique_id
FROM post_techniques pt
JOIN technique_mitigations tm ON tm.technique_id = pt.technique_id
JOIN mitre_mitigations m ON m.mitigation_id = tm.mitigation_id
WHERE pt.raw_post_id IN (?, ?, ...)
```

— a three-table join from posts, through their techniques, to mitigations. Then in Python it counts, **per mitigation, how many distinct posts it would help defend**, and emits:

- `posts_covered` — the distinct-post count
- `post_share` — that count as a fraction of the investigation's total posts
- `techniques` — which T-codes drove the inclusion

sorted `posts_covered` descending. That ranking *is* the feature: "User Training addresses 133 of your 236 posts — do it first." It turns a flat list of countermeasures into a **prioritised remediation plan**.

> **Detour: counting *distinct posts*, not links.**
> A naive `COUNT(*)` over the join would count *technique→mitigation edges*, not posts. If one post maps to three techniques that all share mitigation M1032, that post would inflate M1032's score to 3. Wrong — the analyst cares "how many of my threats does this fix," and that post is one threat. So the helper collects `raw_post_id` into a Python `set` per mitigation and reports `len(set)`. The "what is the unit of the count" question is the single easiest thing to get wrong in an aggregation, and worth pausing on every time.

### 5.3 PDF — `_build_mitigations_html()`

[`backend/api/export.py`](../../backend/api/export.py) reuses `aggregate_mitigations()` (the export module already imports `investigations as inv`) and renders a "Recommended actions" table — same `table.mitre` CSS class as the existing MITRE coverage chart, so it inherits the styling for free, including the bar-fill gradient. The section sits right after "MITRE ATT&CK coverage": the report now reads *threat → coverage → what to do*. The empty case is handled explicitly with a `muted` "no mitigations published" line, so a filter that matches only un-mitigated techniques still produces a clean PDF rather than a blank section.

---

## 6. The frontend: two surfaces, one idea

Both surfaces follow the existing component conventions exactly — `SectionLabel` headers, the `font-mono` / `text-text-muted` type scale, `border-border-soft` cards.

### 6.1 Post detail — "Defensive Recommendations"

[`DetailPanel.tsx`](../../frontend/src/components/DetailPanel.tsx) gains a section after MITRE Techniques. Each `MitigationItem` shows the green `Mxxxx` code, the name, the `addresses` T-codes on the right, a clamped description, and a deep link to `attack.mitre.org`. It renders only when `d.mitigations.length > 0` — a post with no mitigated techniques simply shows no card. **That blank is correct**, not a bug (see §8).

### 6.2 Investigation — "Priority Mitigations"

[`Investigations.tsx`](../../frontend/src/pages/Investigations.tsx) gains a section between the lens summary and the matched-post list. Each `MitigationRow` renders a **coverage bar** — a div whose width is `post_share × 100%` — plus the `posts_covered`/percentage and the driving T-codes. Visually scanning down the bars *is* the prioritised plan: longest bar first. Capped at the top 12 so the panel stays readable.

### 6.3 Types

`api.ts` gains `PostMitigation` and `InvestigationMitigation` interfaces mirroring the two API shapes, wired into `PostDetail.mitigations` and `Investigation.mitigations`. The whole frontend type-checks clean (`tsc --noEmit`, exit 0).

---

## 7. Tech stack & why each piece

| Piece | What it does | Why this and not something else |
|---|---|---|
| MITRE ATT&CK `course-of-action` | The mitigation corpus | Already in the STIX file from Stage 5. Free, official, version-pinned. The authoritative source for "what defends technique X." |
| `mitigates` relationships | The technique↔mitigation edges | The only place MITRE records the mapping. Parsing them ourselves means zero guessing. |
| Raw `sqlite3` join table | Store the many-to-many graph | Same reasoning as `post_techniques`. Queried from both directions → wants a relational shape, not a JSON blob. |
| `ON CONFLICT DO UPDATE` | Idempotent re-ingest | Exact pattern from Stage 5's `ingest()`. Re-runs refresh in place, never error. |
| Plain SQL `GROUP BY` semantics | Investigation ranking | The prioritisation is a counting query. No ML needed — the LLM is *deliberately* not in this path. |
| **No** `sentence-transformers` | — | Mitigations are looked up from a known technique, never searched by similarity. Adding embeddings here would be pure dead weight. |

The headline architectural decision of Stage 5.5 is the one piece of tech **not** used: the LLM. Stage 4 is probabilistic, Stage 5 grounds it. Stage 5.5 is grounding from the very start — there is no guess to correct, because there is no guess. Every recommendation is a row MITRE wrote.

---

## 8. The known-correct quirk: 24 of 35, not 35 of 35

On the demo corpus, **24 of the 35 distinct techniques have mitigations; 11 do not.** A post mapped only to those 11 shows no Defensive Recommendations card.

This is **not a coverage gap to fix.** It is MITRE's deliberate position. The un-mitigated techniques are mostly *discovery* techniques — "Account Discovery," "System Information Discovery," and kin. MITRE's stated view is that you cannot meaningfully *prevent* an adversary from enumerating what is already visible to a legitimate user; the appropriate response is **detection**, not mitigation. So MITRE publishes detection guidance for those techniques but no `course-of-action`.

Surfacing that faithfully — an honest empty state rather than a fabricated "do X" — is the entire point of the no-LLM design. An LLM asked "how do I mitigate Account Discovery?" will *always* produce a confident paragraph. MITRE, correctly, produces nothing. Stage 5.5 shows you what MITRE actually says.

> **Try this:** open `backend/db/sentinelx.db` and run
> `SELECT DISTINCT pt.technique_id FROM post_techniques pt LEFT JOIN technique_mitigations tm ON tm.technique_id = pt.technique_id WHERE tm.technique_id IS NULL`.
> Those are the techniques on your corpus with no published mitigation. Cross-reference a couple on attack.mitre.org — you will find a populated "Detection" section and an empty "Mitigations" one. The data is telling the truth.

---

## 9. How this is used in industry

Closing the threat→countermeasure loop is standard practice in mature CTI tooling — what Stage 5.5 does in miniature, the platforms do at scale:

- **MITRE ATT&CK Navigator** lets analysts overlay mitigations onto the technique matrix and ask "if I deploy M1032, which cells of the matrix go green?" — the same coverage-ranking question `aggregate_mitigations()` answers, visualised on the matrix.
- **D3FEND** is MITRE's *companion* framework dedicated entirely to defensive techniques, with explicit mappings from ATT&CK offensive techniques to D3FEND defensive ones. A heavier version of the technique→mitigation graph we parse.
- **Microsoft Sentinel, CrowdStrike Falcon, SentinelOne** all ship "recommended actions" / "remediation" panels next to every ATT&CK-tagged detection. The analyst is never shown an attack without a paired set of defenses.
- **Recorded Future, Mandiant Advantage** publish intelligence reports where each technique callout is accompanied by mitigation guidance — a finished CTI report is expected to be *actionable*, not merely descriptive.
- **Risk-prioritised remediation** — counting how many incidents a single control would address, and ranking controls by that count, is the core of every "what should we fix first" exercise in security operations. Our `posts_covered` ranking is exactly that pattern at corpus scale.

The verify-then-recommend arc across Stages 4 → 5 → 5.5 mirrors how a real analyst works: hypothesise the techniques, validate them against the corpus, *then* pull the corpus's own guidance on what to do. We have now automated all three.

---

## 10. What Stage 5.5 does **not** do (deferred or out of scope)

- **LLM-tailored phrasing.** We surface MITRE's generic mitigation text verbatim. A future option (explicitly considered and deferred) would feed the Mxxxx descriptions to Mistral as *authoritative context* and have it write a post-specific paragraph — "for this credential-sale thread, prioritise MFA because…". Grounded re-phrasing, not invention. Deferred so the first version stays provably hallucination-free.
- **Detection guidance.** Techniques with no mitigation still have a MITRE "Detection" section (`x_mitre_data_component` objects). Stage 5.5 ignores those. Surfacing detection alongside mitigation — so the 11 un-mitigated techniques still carry advice — is a natural follow-up.
- **Control-framework cross-walks.** MITRE maps mitigations to NIST 800-53 control ids. We do not ingest those. An enterprise user might want "M1032 → which NIST controls" — a future join table.
- **Mitigation embeddings.** Deliberately omitted. Mitigations are reached *through* a known technique; there is no text-similarity query against them. If a future feature needs free-text search over mitigations, a `post_embeddings`-style table would be added then — not pre-emptively.

---

## 11. Gotchas observed during this stage

1. **The aggregation unit.** As in §5.2 — counting join rows instead of distinct posts silently inflates every score. The fix (a Python `set` of `raw_post_id`) is trivial; *noticing* the bug requires asking "what is one unit of this count" before writing the `COUNT`.
2. **FK order in `--reset-corpus`.** `technique_mitigations` references `mitre_mitigations`. Deleting the parent first throws an FK error. The reset deletes children first; obvious in hindsight, easy to write backwards.
3. **`--ingest-mitigations` before `--ingest`.** Run on an empty `mitre_techniques`, every link's FK fails. `parse_mitigations()` filters against `valid_tcodes` from the *same file*, so parsing is fine — but the in-`ingest_mitigations()` `known`-set check is what turns a would-be `IntegrityError` into a clear logged warning. Run `--ingest` (or the bundled call) at least once first.
4. **One mitigation, many techniques → duplicate rows.** Both API helpers must dedupe by `mitigation_id`. The per-post helper folds duplicates into one entry with an `addresses` list; the investigation helper folds them into one entry with a post `set`. Forgetting the dedupe yields a UI card that lists "M1032" five times.
5. **Empty PDF section.** A filter matching only un-mitigated techniques yields zero mitigations. `_build_mitigations_html()` handles that with an explicit `muted` line — without it, the PDF would have a header followed by nothing.

---

## 12. End-of-stage status

- ✅ Schema for `mitre_mitigations` + `technique_mitigations` shipped in `backend/db/schema.sql`, additive, no migration.
- ✅ Ingest path: `parse_mitigations()` + `ingest_mitigations()` + `--ingest-mitigations`. Offline, embedding-free, idempotent. 44 mitigations, 1448 links, 0 skipped.
- ✅ API: `/posts/{id}` returns deduped per-post `mitigations[]`; `/investigations/{id}` returns coverage-ranked `mitigations[]`.
- ✅ PDF: "Recommended actions" section renders; verified 200 + valid PDF.
- ✅ Frontend: "Defensive Recommendations" card and "Priority Mitigations" coverage list; `tsc --noEmit` clean.
- ✅ Existing 236 posts / 295 `post_techniques` rows untouched — every change additive.
- ⬜ Optional follow-ups: LLM-tailored phrasing, detection guidance for un-mitigated techniques, NIST control cross-walk.

SentinelX's reports now answer both halves of the analyst's question: Stage 5 says *what the threat is*, Stage 5.5 says *what to do about it* — and says it with MITRE's own words, never a model's.
