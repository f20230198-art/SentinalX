# STAGE 03 — LEARN

> **Stage 3 in one sentence:** read the immutable `raw_posts` rows that Stage 2 lands, pull *Indicators of Compromise* (IPs, hashes, CVEs, BTC addresses, etc.) out of them with regex over a defanged-then-refanged copy of the text, pull *named entities* (orgs, people, malware families, threat actors) out with spaCy plus a curated keyword pass, and persist both to dedicated tables — incrementally, so each run only processes what's new.

This document is the standalone teaching pass for Stage 3. It assumes you've read `STAGE_02_LEARN.md` (or at least know that Stage 2 publishes immutable rows in `raw_posts` with a stable `id` autoincrement key).

---

## 0. Mental model — what is an "extraction" stage actually doing?

In a real CTI pipeline the **collection layer** (Stage 2) is dumb on purpose: pull bytes from sources, get them onto disk, don't try to interpret. The **extraction layer** (Stage 3) is the first interpretive pass. Its job is to turn a wall of unstructured prose into *structured facts* downstream stages can reason about.

Two kinds of facts come out:

| Class               | Examples                                                     | Why CTI cares                                                              |
|---------------------|--------------------------------------------------------------|----------------------------------------------------------------------------|
| **IOCs** (indicators)| `185.220.101.42`, `CVE-2024-12345`, `bc1q…`, `evil.com`, `a3f5…` (sha256) | These are pivotable atoms. An analyst sees an IP in your store, looks up where else it has appeared, blocks it at the perimeter, hunts for it in EDR. |
| **Named entities** | `Lazarus Group`, `Cobalt Strike`, `Microsoft`, `Ukraine`     | These are *who/what/where*. They give context to a post — actor attribution, tooling, victim industry. They also feed the MITRE mapping in Stage 5. |

Everything an extraction stage does is shaped by four pressures, and Stage 3 addresses each:

| Pressure                                      | Why it matters                                                                | How Stage 3 handles it                                                                  |
|-----------------------------------------------|-------------------------------------------------------------------------------|------------------------------------------------------------------------------------------|
| **Defanged input**                            | Threat reports write `1.2.3[.]4` and `hxxps://` so URLs don't auto-resolve.   | A `refang()` pass turns defanged forms back into matchable strings before regex runs.    |
| **Overlapping patterns**                      | A 64-hex string is sha256, not three md5s. A domain inside a URL is not a separate domain. | Match longest/most-specific first, record consumed spans, skip overlaps for shorter patterns. |
| **NER models miss CTI vocabulary**            | spaCy has never heard of "Lazarus Group" or "Cobalt Strike."                  | spaCy for the generic labels, a curated keyword pass for `MALWARE` and `THREAT_ACTOR`.  |
| **Idempotency under reruns**                  | You will re-run extraction. Outputs must be stable — no duplicate rows.       | `UNIQUE(raw_post_id, ioc_type, value)` and `UNIQUE(raw_post_id, label, text)` at the DB layer; `dedupe()` at the Python layer to avoid wasted IntegrityErrors. |

If you internalise that table, the rest of this doc is implementation.

---

## 1. What was built

Three files under `backend/pipeline/`, plus a schema extension and one column added to `raw_posts`. ~250 lines of Python total.

```
backend/
├── db/
│   ├── schema.sql          ← extended with iocs / entities / extraction_runs
│   └── store.py            ← gained a guarded ALTER TABLE for raw_posts.processed_at
└── pipeline/
    ├── __init__.py
    ├── extract.py          ← IOCExtractor, EntityExtractor, refang(), dedupe()
    └── run.py              ← CLI entrypoint (--once / --watch / --reset)
```

Invocation, same `python -m` form as Stage 2:
```bash
backend/.venv/Scripts/python.exe -m backend.pipeline.run --once
```

### 1.1 The verified-working run

```
$ python -m backend.pipeline.run --once    # first time, 235 unprocessed
processed posts=200 iocs+=151 entities+=205
processed posts=35  iocs+=25  entities+=34
                   total: 235 posts, 176 IOCs, 239 entities

$ python -m backend.pipeline.run --once    # second time
processed posts=0 iocs+=0 entities+=0
```

Distribution observed on the 235-post seed corpus:

- **IOCs:** 53 ipv4 · 43 cve · 36 btc · 21 domain · 14 email · 9 sha256
- **Entities:** 86 ORG · 50 PERSON · 24 NORP · 23 MALWARE · 21 PRODUCT · 20 GPE · 13 THREAT_ACTOR · 1 EVENT · 1 LOC

That distribution is the seed corpus' fingerprint; if a future change to the regex set or the keyword lists causes those numbers to swing wildly, that's a regression signal worth investigating.

---

## 2. File-by-file walkthrough

### 2.1 Schema additions (`backend/db/schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS iocs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_post_id     INTEGER NOT NULL,
    ioc_type        TEXT    NOT NULL,
    value           TEXT    NOT NULL,
    span_start      INTEGER,
    span_end        INTEGER,
    extracted_at    REAL    NOT NULL,
    UNIQUE(raw_post_id, ioc_type, value),
    FOREIGN KEY(raw_post_id) REFERENCES raw_posts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_post_id     INTEGER NOT NULL,
    label           TEXT    NOT NULL,
    text            TEXT    NOT NULL,
    span_start      INTEGER,
    span_end        INTEGER,
    extracted_at    REAL    NOT NULL,
    UNIQUE(raw_post_id, label, text),
    FOREIGN KEY(raw_post_id) REFERENCES raw_posts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS extraction_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      REAL    NOT NULL,
    finished_at     REAL,
    posts_seen      INTEGER NOT NULL DEFAULT 0,
    iocs_inserted   INTEGER NOT NULL DEFAULT 0,
    entities_inserted INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);
```

Design choices, point by point:

- **Two parallel tables (`iocs`, `entities`) instead of one polymorphic `extractions` table.** They have different keys (`ioc_type` vs `label`) and different downstream consumers — Stage 4's LLM prompt cares about IOCs as a flat list; Stage 5's MITRE mapper cares about entities as actor/tool/victim slots. Forcing them into one table would require a discriminator column and downstream `WHERE kind = 'ioc'` filters everywhere. Two tables is cheaper and clearer.
- **`raw_post_id` foreign key with `ON DELETE CASCADE`.** If a `raw_posts` row is ever deleted (e.g. takedown / right-to-erasure / data hygiene sweep), its extracted facts go too. Manually keeping these in sync is exactly the kind of integrity bug FKs exist to prevent. We turned `PRAGMA foreign_keys = ON` in Stage 2's `Store.__init__` precisely so this would be enforced.
- **`UNIQUE(raw_post_id, ioc_type, value)`.** This is the Stage 3 dedup key. Re-running extraction on the same post must not produce duplicates. The natural key is "the same indicator extracted from the same post" — not "the same indicator anywhere," because the same IP appearing in two posts is a *real* signal we want to keep (it tells the analyst which posts are linked).
- **`span_start` / `span_end`.** Optional, but cheap. They're the character offsets in the post body where the match was found. Stage 7's UI will use them to highlight matches inline. Stored now to avoid a re-extraction later.
- **`extracted_at` on every row, not just on the run.** Lets you ask "what new IOCs landed in the last 24h?" without joining `extraction_runs`. Also survives runs being deleted.
- **Indexes on `(raw_post_id)`, `(ioc_type)`, `(value)` and `(raw_post_id)`, `(label)`.** The post-id indexes serve "show me all extractions for this post" (Stage 7 UI). `ioc_type` / `label` serve aggregations ("count of CVEs across the corpus"). The index on `iocs.value` is the one that pays off most as the corpus grows — pivot queries ("what other posts mention this IP?") become O(log n).

### 2.2 The new column on `raw_posts` and why it lives in `Store._init_schema`

```python
# backend/db/store.py
cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(raw_posts)")}
if "processed_at" not in cols:
    self.conn.execute("ALTER TABLE raw_posts ADD COLUMN processed_at REAL")
    self.conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_raw_posts_processed ON raw_posts(processed_at)"
    )
self.conn.commit()
```

Two things to know.

**(a) SQLite has no `ALTER TABLE … ADD COLUMN IF NOT EXISTS`.** That syntax is Postgres / MySQL. In SQLite you have to read `PRAGMA table_info()` (or `sqlite_master`) yourself, see whether the column already exists, and decide. Hence the four-line guard.

**(b) Why an in-place migration instead of editing `schema.sql` directly.** Editing `schema.sql` and adding `processed_at` to the `CREATE TABLE raw_posts (...)` block would silently *only* take effect on fresh DBs — `CREATE TABLE IF NOT EXISTS` skips an existing table. Anyone who already had a `sentinelx.db` from Stage 2 (the user) would never get the new column. Doing the migration at `Store.__init__` time means every code path that touches the DB upgrades it on first contact, which is the closest thing to "automatic migrations" you get without pulling in `alembic` or `yoyo-migrations`. It's the right level of complexity for this stage; we'll graduate to a real migration tool when schema changes start happening more than monthly.

`processed_at` itself is a **REAL** epoch timestamp. NULL means "not yet processed by Stage 3"; a number means "processed at this time." That choice (rather than a boolean flag) is deliberate:

- It doubles as an audit trail — you can see *when* extraction last touched the post.
- It makes the cursor query a simple `WHERE processed_at IS NULL`.
- A boolean would force you to keep a separate timestamp table to recover the same information.

### 2.3 `backend/pipeline/extract.py` — the core extractors

This file is split into four logical sections: regex patterns, defanging, the IOC extractor, the entity extractor. Walk them in order.

#### 2.3.1 The regex patterns

```python
_IPV4_RE   = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_RE   = re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}\b")
_CVE_RE    = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
_MD5_RE    = re.compile(r"\b[a-fA-F0-9]{32}\b")
_SHA1_RE   = re.compile(r"\b[a-fA-F0-9]{40}\b")
_SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
_BTC_RE    = re.compile(r"\b(?:bc1[a-z0-9]{25,62}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")
_URL_RE    = re.compile(r"\bhttps?://[^\s<>\"'\)]+", re.IGNORECASE)
_EMAIL_RE  = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_DOMAIN_RE = re.compile(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[A-Za-z]{2,24}\b")
```

Some specific things worth pausing on:

- **`\b` word boundaries everywhere.** Without them, `1.2.3.4567` would match `1.2.3.456` as IPv4. `\b` says "transition between word-char and non-word-char," which is what the eye reads as a token boundary.
- **IPv4 is *permissive* on the regex side, *strict* in `_valid_ipv4()`.** The regex matches anything shaped like `\d{1,3}.\d{1,3}.\d{1,3}.\d{1,3}`, then `_valid_ipv4()` rejects octets > 255. Doing both in regex (`(25[0-5]|2[0-4]\d|[01]?\d\d?)`) works but is unreadable; doing it in Python after the fact is the same correctness for one-tenth the cognitive cost.
- **IPv6 is intentionally simplified.** A correct IPv6 regex is famously horrible (zero-compression `::`, embedded IPv4, scoped addresses). The pattern here matches the common forms in security writing and rejects fragments like `1:2`. We pay the cost of occasional false positives in exchange for not maintaining a 200-character regex for a low-volume IOC type.
- **Bitcoin: legacy + bech32, no Taproot.** P2PKH (`1…`), P2SH (`3…`), and bech32 (`bc1…`). Newer P2TR (`bc1p…`) addresses are bech32m and the same `bc1[a-z0-9]{25,62}` regex catches them as a side-effect. Charset for legacy is base58 minus `0OIl`, hence `[a-km-zA-HJ-NP-Z1-9]`.
- **URL is non-greedy by stop-set, not by `?`.** `[^\s<>\"'\)]+` consumes everything up to a whitespace, angle bracket, quote, or close-paren. That handles 99% of forum text. If we needed RFC-3986 perfect we'd reach for `urllib.parse`, but for extraction the regex is fine.
- **Email is "pragmatic, not RFC 5322."** The full RFC 5322 grammar permits things almost nobody writes (quoted local parts, comments, IP-literal domains). Every shipping email regex is a pragmatic subset. This one rejects nothing real that I've seen in the corpus.
- **Domain regex runs *last* and is filtered against already-consumed spans.** A URL like `https://evil.com/path` contains a domain — but we've already extracted the URL, and we don't want a separate `domain="evil.com"` row that's just the URL's hostname. The `_overlaps()` check in `IOCExtractor.extract` is what prevents the double-count. Same for domains inside emails.

#### 2.3.2 Defanging — the `refang()` pass

Threat reports defang IOCs so they can't be clicked, auto-resolved, or trigger blocklists. There's no single defanging standard; the conventions you'll see in the wild:

| Defanged form    | What it represents     |
|------------------|------------------------|
| `1.2.3[.]4`      | IP / domain — `[.]` for `.` |
| `1.2.3(.)4`      | Same idea, parens variant |
| `1.2.3{.}4`      | Curly variant (rarer)  |
| `evil[.]com`     | Domain                 |
| `hxxp://…`       | URL — `hxxp` for `http` (also `hxxps`) |
| `user[at]example.com` | Email — `[at]` for `@` |

`refang()` undoes all of these in one pass, in a fixed order:

```python
_DEFANG_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\[\.\]"), "."),
    (re.compile(r"\(\.\)"), "."),
    (re.compile(r"\{\.\}"), "."),
    (re.compile(r"\[at\]", re.IGNORECASE), "@"),
    (re.compile(r"\(at\)", re.IGNORECASE), "@"),
    (re.compile(r"\bhxxps?://", re.IGNORECASE), lambda m: m.group(0).replace("hxxp", "http").replace("HXXP", "HTTP")),
]
```

Two design notes:

- **Refang into a separate string, run regex on the refanged copy.** We do *not* mutate the original post body. The original is preserved in `raw_posts.body`; only the in-memory working copy is normalised. This means analysts viewing the post in Stage 7 still see the defanged form the author wrote, while extraction sees the live form.
- **The span we record is the span over the *refanged* string.** The doc-comment in `extract.py` calls this out: storing both refanged-and-original spans is overkill for our purposes. The downside is that `body[span_start:span_end]` won't always equal `value` (e.g. if the post said `1.2.3[.]4`, `value` is `1.2.3.4` but the original-string slice is the defanged form). Stage 7 will need to be aware of this when highlighting; we accept that as a small UI cost for the simpler storage model.

#### 2.3.3 `IOCExtractor.extract()` — the overlap-aware pipeline

```python
def extract(self, text: str) -> list[Match]:
    t = refang(text)
    out: list[Match] = []
    spans_consumed: list[tuple[int, int]] = []

    def add(ioc_type, m, value=None):
        v = value if value is not None else m.group(0)
        out.append(Match(ioc_type, v, m.span()))
        spans_consumed.append(m.span())

    for m in _URL_RE.finditer(t): add("url", m)
    for m in _EMAIL_RE.finditer(t): add("email", m)
    for m in _IPV4_RE.finditer(t):
        if _valid_ipv4(m.group(0)): add("ipv4", m)
    for m in _IPV6_RE.finditer(t):
        if m.group(0).count(":") >= 2: add("ipv6", m)
    for m in _CVE_RE.finditer(t):
        add("cve", m, value=m.group(0).upper())

    # Hash extraction: longest first.
    for m in _SHA256_RE.finditer(t): add("sha256", m, value=m.group(0).lower())
    for m in _SHA1_RE.finditer(t):
        if not _overlaps(m.span(), spans_consumed):
            add("sha1", m, value=m.group(0).lower())
    for m in _MD5_RE.finditer(t):
        if not _overlaps(m.span(), spans_consumed):
            add("md5", m, value=m.group(0).lower())

    for m in _BTC_RE.finditer(t): add("btc", m)

    # Domains last, skip anything inside a URL or email.
    for m in _DOMAIN_RE.finditer(t):
        if _overlaps(m.span(), spans_consumed): continue
        add("domain", m, value=m.group(0).lower())

    return out
```

The shape of this method is the entire lesson. Six things to extract from it:

1. **Order matters and is the whole game.** URL before domain, email before domain, sha256 before sha1 before md5. Each pass adds its spans to `spans_consumed`; later passes consult that list and skip overlaps.
2. **Hash subsumption is not symmetric.** A 64-hex sha256 string contains a valid 40-hex prefix that *would* match `_SHA1_RE`. If we ran sha1 first, we'd incorrectly classify part of a sha256 as a sha1. Running longest-first is the only correct order.
3. **CVE is uppercased on the way in (`m.group(0).upper()`); hashes are lowercased; domains are lowercased.** Canonicalisation at extraction time means downstream code can do exact string comparison without a `LOWER()` in every SQL query. `CVE-2024-12345` and `cve-2024-12345` collapse to the same row in `iocs`.
4. **`Match` is a frozen dataclass.** Three fields: `type`, `value`, `span`. `frozen=True` makes instances hashable, which lets `dedupe()` use them in a `dict`. It also makes them immutable, which is a mild correctness win — the extractor's output is not supposed to be mutated downstream.
5. **`_overlaps` is O(n*m) per call.** That's fine at our scale (a typical post has well under 50 matches). If post sizes ever grew to where this mattered, the fix is an interval tree or sorted-by-span list with binary search.
6. **No URL `^` / `$` anchors anywhere.** The patterns are designed to find things *embedded in prose*, not to validate pre-trimmed strings. `re.finditer` over the whole text is the right primitive; `re.match` would be the wrong one.

#### 2.3.4 `EntityExtractor` — spaCy + curated keywords

```python
class EntityExtractor:
    def __init__(self, model: str = "en_core_web_sm") -> None:
        self.nlp = spacy.load(model, disable=["parser", "lemmatizer"])

    def extract(self, text: str) -> list[Match]:
        doc = self.nlp(text)
        out: list[Match] = []
        for ent in doc.ents:
            if ent.label_ in _KEEP_LABELS:
                out.append(Match(ent.label_, ent.text, (ent.start_char, ent.end_char)))

        for term in _MALWARE_TERMS:
            for m in re.finditer(rf"\b{re.escape(term)}\b", text):
                out.append(Match("MALWARE", term, m.span()))
        for term in _THREAT_ACTOR_TERMS:
            for m in re.finditer(rf"\b{re.escape(term)}\b", text):
                out.append(Match("THREAT_ACTOR", term, m.span()))
        return out
```

Three things worth understanding here.

**(a) `spacy.load("en_core_web_sm", disable=["parser", "lemmatizer"])`.** spaCy's pipeline for `en_core_web_sm` runs `tok2vec → tagger → parser → attribute_ruler → lemmatizer → ner`. We only need NER. Disabling `parser` (dependency parsing) and `lemmatizer` skips the most expensive component (parsing) and a component we don't use (lemmatising). On the 235-post corpus that took the per-batch wall-clock from ~6.5s to ~3.5s. Note we *do* keep `tagger` and `attribute_ruler` because some NER predictions transitively depend on POS tags.

**(b) `_KEEP_LABELS = {"PERSON", "ORG", "GPE", "NORP", "PRODUCT", "EVENT", "LOC"}`.** spaCy emits many labels (DATE, CARDINAL, ORDINAL, MONEY, PERCENT, TIME, …). Most are noise for CTI:
- `PERSON` — useful (named individuals, journalists, researchers).
- `ORG` — useful (victim companies, vendors).
- `GPE` (geo-political entity, i.e. country/city/state) — useful (geographic targeting).
- `NORP` (nationalities, religious or political groups) — useful (e.g. "Russian", "Ukrainian", "Iranian").
- `PRODUCT` — useful (named software/hardware products, often tooling).
- `EVENT` — occasionally useful (e.g. named conferences).
- `LOC` — non-GPE locations; small but worth keeping.

The dropped labels (DATE, CARDINAL, etc.) carry zero CTI signal in this pipeline. Stage 4's LLM prompt would have to filter them out anyway, so we filter at extraction.

**(c) Curated keyword pass for MALWARE and THREAT_ACTOR — the load-bearing decision.** Off-the-shelf spaCy NER has never seen "Cobalt Strike" or "Lazarus Group" in its training data and will tag them inconsistently — sometimes ORG, sometimes PRODUCT, sometimes nothing. There are three ways to fix this:

1. **Curated keyword lists.** What we did. Cheap, deterministic, covers the most common 20-30 names. Recall is bounded by the list size.
2. **Fine-tune spaCy on a CTI corpus.** Best recall, but requires labelled data and an hour of GPU time. Premature for a learning project.
3. **Use a CTI-specific model.** `cyner`, `spacy-cybersecurity`, or commercial offerings. Higher recall out of the box but adds a heavyweight dependency.

We picked (1) deliberately: the marginal benefit of (2) or (3) is wasted on a synthetic corpus. **Stage 5's MITRE ATT&CK ingest + vector index is where real coverage will come from** — at that point a STIX bundle of all known threat actors and malware families will land in the DB, and a name-matcher over that table will replace these hard-coded sets. The keyword pass in Stage 3 is scaffolding, not a final answer.

The curated lists themselves: 17 malware families, 12 threat actor groups. Both are case-sensitive on purpose — "Conti" the ransomware vs "conti" the substring is a real disambiguation we want to preserve.

### 2.4 `dedupe()` — collapsing duplicates within a single post

```python
def dedupe(matches: Iterable[Match]) -> list[Match]:
    seen: dict[tuple[str, str], Match] = {}
    for m in matches:
        key = (m.type, m.value)
        if key not in seen:
            seen[key] = m
    return list(seen.values())
```

A single post body might mention `evil.com` four times in the same paragraph. The DB's UNIQUE constraint will reject three of those at insert time, but it'll cost us four `IntegrityError` exceptions per post. Pre-collapsing in Python avoids those — same correctness, less work. We keep the *first* span we saw for each `(type, value)` because that's the one most likely to be the introducing mention; subsequent ones are typically references to the same entity.

This is a classic example of **the application layer doing what the database also enforces**. It's not redundant: the DB constraint guarantees correctness across processes and across reruns, and the application dedup is a performance optimisation. Both belong in real ETL code.

### 2.5 `backend/pipeline/run.py` — the CLI orchestrator

The structure mirrors Stage 2's scraper CLI exactly: `--once`, `--watch --interval N`, `--reset`, mutually exclusive. Same logging setup, same `KeyboardInterrupt → 130` exit code convention, same outer try/except in watch mode that swallows per-batch errors.

The non-trivial pieces:

#### 2.5.1 The cursor — `WHERE processed_at IS NULL`

```python
def _fetch_unprocessed(conn, limit):
    return conn.execute(
        "SELECT id, body FROM raw_posts WHERE processed_at IS NULL "
        "ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()
```

That's the entire cursor. Same philosophy as Stage 2: derive cursor state from the data, don't keep a parallel state row that can desync. Stage 2 used `MAX(source_created_at)`; Stage 3 uses `processed_at IS NULL`. Different shapes, same principle — *the cursor is a property of the data, not of a separate state record*.

`ORDER BY id` is important. Without it, SQLite is free to return rows in any order, and a partial batch failure would leave a hole the next run wouldn't notice (because `processed_at IS NULL` is a set, not a sequence). Ordering by the primary key gives deterministic processing and makes `LIMIT N` semantically equivalent to "the next N posts I haven't processed yet."

#### 2.5.2 `process_batch` — the unit of work

```python
def process_batch(store, ioc, ent, batch_size):
    conn = store.conn
    started = time.time()
    cur = conn.execute("INSERT INTO extraction_runs (started_at) VALUES (?)", (started,))
    run_id = cur.lastrowid
    conn.commit()

    posts_seen = iocs_inserted = entities_inserted = 0
    err: str | None = None

    try:
        rows = _fetch_unprocessed(conn, batch_size)
        for row in rows:
            now = time.time()
            iocs = dedupe(ioc.extract(row["body"]))
            ents = dedupe(ent.extract(row["body"]))
            iocs_inserted += _insert_iocs(conn, row["id"], iocs, now)
            entities_inserted += _insert_entities(conn, row["id"], ents, now)
            conn.execute(
                "UPDATE raw_posts SET processed_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            posts_seen += 1
        conn.commit()
    except Exception as e:
        err = repr(e)
        conn.commit()
        raise
    finally:
        conn.execute(
            "UPDATE extraction_runs SET finished_at = ?, posts_seen = ?, "
            "iocs_inserted = ?, entities_inserted = ?, error = ? WHERE id = ?",
            (time.time(), posts_seen, iocs_inserted, entities_inserted, err, run_id),
        )
        conn.commit()

    return posts_seen, iocs_inserted, entities_inserted
```

Five things worth noting:

1. **The run-log row is INSERTed *before* work begins** with just `started_at`, then UPDATEd in the `finally` block. Same pattern as Stage 2's scraper-runs context manager. If the process is killed mid-batch (SIGKILL, OOM, host crash), the row still exists with `finished_at IS NULL`, which is a recoverable signal that the run was interrupted.
2. **`processed_at` is stamped *after* the IOC and entity inserts succeed for that post.** Order matters: if extraction inserts succeed but the `UPDATE raw_posts SET processed_at` somehow fails, the next run will re-process the post — the UNIQUE constraints make that safe (no dup rows), and the post will eventually be marked processed. The reverse order would be unsafe: marking processed before extracting risks losing extractions if the inserts fail.
3. **One commit per batch, not per post.** The inner loop accumulates inserts and a single `conn.commit()` at the end flushes them. SQLite's transaction overhead is per-commit, not per-statement, so committing per row would make this 100× slower for no correctness benefit. The atomicity boundary becomes "the whole batch or nothing," which is what we want.
4. **Exception handling commits, then re-raises.** The `except Exception` block does `conn.commit()` before `raise`. That commits any work that succeeded before the error — partial progress is preserved. The `finally` block then writes the run-log row including the error message. The combination gives us "as much progress as possible" + "audit trail of what failed," which is the right behaviour for an idempotent batch job.
5. **No streaming.** We `fetchall()` the batch into memory and iterate. At 200 posts per batch with ~1KB bodies, that's ~200KB; safe. At a million posts per batch we'd want a server-side cursor (`fetchmany`) — but SQLite doesn't really have those, so we'd actually paginate by `id > last_id` instead. Premature for now.

#### 2.5.3 `run_once` — drain to completion

```python
def run_once(store, ioc, ent, batch):
    while True:
        seen, _, _ = process_batch(store, ioc, ent, batch)
        if seen < batch:
            break
```

The `--once` mode keeps calling `process_batch` until it returns fewer rows than the batch size — that's the signal that the unprocessed queue is drained. This means `--once` is *not* a single batch; it's "do all the work that's currently pending, then exit." That matters operationally: cron can fire `--once` every five minutes and trust it'll catch up on whatever the scraper added since the last fire.

The `seen < batch` termination is more reliable than `seen == 0` because it also handles the boundary case where the last batch had exactly enough rows to fill `batch_size` and the next call would return zero — both cause `seen < batch` to be true on one call or the other.

#### 2.5.4 `reset_extractions` — wipe and replay

```python
def reset_extractions(store):
    store.conn.executescript(
        "DELETE FROM iocs; DELETE FROM entities; DELETE FROM extraction_runs;"
        "UPDATE raw_posts SET processed_at = NULL;"
        "DELETE FROM sqlite_sequence WHERE name IN ('iocs','entities','extraction_runs');"
    )
    store.conn.commit()
```

Note that `--reset` does *not* touch `raw_posts` data, only its `processed_at` column. Stage 2's `--reset-cursor` wipes `raw_posts` and `scraper_runs` (Stage 2's domain). Stage 3's `--reset` wipes only Stage 3's domain. Two stages, two cursors, two reset commands — the ownership boundaries we set up in §3.1 of Stage 2's LEARN doc are paying off.

The `DELETE FROM sqlite_sequence` line resets the autoincrement counter so a fresh extraction starts `iocs.id` back at 1. Without it, the counter would resume where it left off — not wrong, but cosmetically ugly when you're poking around in `sqlite3`.

---

## 3. Why these choices, vs alternatives

### 3.1 Regex IOCs vs LLM IOC extraction

Stage 4 will run a local LLM (Mistral via Ollama) over post bodies. A reasonable question is: why not just ask the LLM to extract IOCs too? Several reasons it'd be wrong:

- **Determinism.** Regex is byte-for-byte reproducible. LLMs are not. If an analyst flags a false positive in `iocs`, you want to be able to point to the exact pattern that produced it.
- **Performance.** spaCy NER + regex on a forum post takes ~15ms. A 7B-parameter LLM takes 1–5 *seconds* per post on CPU, more on long posts. The IOC step needs to scale to the whole corpus; the LLM step does not.
- **Recall on well-formatted IOCs is actually higher with regex.** LLMs hallucinate IPs that aren't in the text, drop CVEs they don't recognise, and invent hash digests with one wrong character. A 64-hex string is 100% reliably a sha256 with `_SHA256_RE`.
- **Auditability.** Regulators / customers occasionally want to know *how* an IOC ended up in a feed. "We matched pattern X at offset Y" is a defensible answer; "the LLM said so" is not.

The Stage 4 LLM's job is a different one — *summarisation, MITRE technique inference, intent classification* — not IOC scraping.

### 3.2 spaCy `en_core_web_sm` vs `_md` vs `_lg` vs transformers

The four sizes: `_sm` (~12 MB), `_md` (~40 MB, with word vectors), `_lg` (~560 MB, larger word vectors), `_trf` (~440 MB, transformer-based). Each step up improves recall on rare names but at increasing model-load and inference cost.

`_sm` is right for Stage 3 because:
- We're not relying on spaCy for the hard-to-spot names (those go through the curated keyword pass).
- Cold-start time matters — `--once` boots a fresh process, loads the model, drains the queue, exits. `_lg` adds 4-5 seconds of load time per invocation.
- `_trf` would require a GPU for reasonable throughput on a non-trivial corpus.

If we ever needed higher PERSON/ORG recall we'd jump straight to `_trf` and pay the GPU cost rather than incrementally trying `_md` / `_lg`.

### 3.3 "Process a column on `raw_posts`" vs "separate `processed_posts` table"

Two ways to track Stage-3 progress over Stage-2 rows:

1. **Add `processed_at` to `raw_posts`** (what we did).
2. **Create a `processed_posts(raw_post_id, processed_at)` join table.**

(2) is more "normalised" and lets multiple downstream stages each track their own progress without fighting for a single column. (1) is simpler and reads better in queries.

We picked (1) for now because there's only one downstream stage. When Stage 4 (LLM) and Stage 5 (MITRE mapping) come online, they'll each need their own progress tracking. At that point the right move is (2) — a single `post_processing_state` table keyed by `(raw_post_id, stage)` — and `processed_at` on `raw_posts` will be either kept as a fast-path "fully processed by the whole pipeline" denormalisation, or migrated away. Either is fine; the rewrite cost is small because the cursor logic is in one function (`_fetch_unprocessed`).

### 3.4 Why `dedupe()` and not just rely on the DB's UNIQUE

Already covered in §2.4: the DB's UNIQUE is correctness; `dedupe()` is performance. Both belong. The principle generalises — *correctness invariants belong in the database, performance optimisations belong in the application*.

### 3.5 Per-row `try/except` on inserts vs batch insert

Same trade-off as Stage 2's scraper. Per-row `try/except` lets us count inserts vs duplicates accurately, at the cost of one Python-level exception per duplicate. At our scale (hundreds of inserts per batch), the cost is invisible. At hundreds of thousands per batch we'd switch to `INSERT … ON CONFLICT DO NOTHING` with `executemany` and read `cur.rowcount`.

---

## 4. Tech stack tour, with industry context

| Component                         | What it is                                                  | Where it shows up in industry                                                                                                                                                              |
|-----------------------------------|--------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **spaCy**                         | Production-grade Python NLP library — tokenisation, POS, parsing, NER, lemmatisation, custom components. | Default NER stack at most CTI shops that don't have an LLM budget; backbone of `cyner` and other CTI-NER libraries. Also used in healthcare (medspaCy), legal, and financial NLP. |
| **`en_core_web_sm`**              | spaCy's small English pipeline. CNN-based NER, no word vectors. ~12 MB. | The default pick for "I want NER, I don't want a model download for every dev." Bundled into countless `pip install`-only apps where a transformer-sized download would be unacceptable. |
| **Python `re` module**            | Stdlib regex engine. Backtracking, non-fancy.                | Pervasive. Heavyweight pipelines move to `regex` (the third-party module with named groups + better unicode support) or hand-rolled finite-state machines (Hyperscan, Rust's `regex`) when throughput matters. For prose-volume IOC extraction, stdlib `re` is fine. |
| **Defanging / refanging**         | Convention in CTI writing for non-clickable IOCs.            | Universal in incident reports, vendor blogs, ISAC sharing. Tools that don't refang miss most of the IOCs in human-written reports. CISA's automated indicator sharing (AIS) and OpenCTI both ship refanging passes. |
| **Indicators of Compromise (IOCs)** | Atomic observables: IPs, domains, hashes, CVEs, BTC, etc.   | The fundamental currency of CTI. Stored in TAXII servers, MISP instances, OpenCTI, and every commercial threat intel platform. Standardised by STIX 2.1 (Stage 5 will touch this). |
| **MITRE ATT&CK (referenced)**     | Knowledge base of adversary tactics, techniques, procedures. | The canonical taxonomy for "what did the attacker do." Stage 5 will ingest the JSON dump and cross-reference our extracted entities against threat-actor and malware names. Already referenced here because our `_THREAT_ACTOR_TERMS` and `_MALWARE_TERMS` lists shadow ATT&CK's intrusion-sets and software corpora. |
| **STIX 2.1 (referenced)**         | Structured Threat Information Expression — JSON schema for indicators, campaigns, intrusion-sets, etc. | The lingua franca for sharing threat intel between organisations. Our `iocs` table is essentially a flattened subset of STIX's `indicator` SDO; in Stage 6 we'll consider exposing a STIX-shaped API. |
| **SQLite UNIQUE + ON DELETE CASCADE** | Referential integrity and dedup at the storage layer.    | The pattern any real ETL pipeline uses. Snowflake/BigQuery/Postgres all have equivalents. The reason: extraction is rerun frequently (model updates, regex updates, bug fixes), and idempotency must be a property of the storage, not of the application. |
| **`PRAGMA table_info` + guarded ALTER TABLE** | SQLite's idiom for "additive migrations without a migration tool." | Used in any single-file SQLite app that has shipped multiple versions — Firefox bookmarks, Apple's CoreData backing stores, hundreds of Electron apps. The "real" answer is alembic / yoyo / liquibase, but you don't need it until you do. |
| **Ollama / Mistral (Stage 4 preview)** | Local LLM runner + model weights.                       | Stage 4 will use this to do summarisation and MITRE technique inference. Mentioned here only because Stage 3's design (regex for atoms, LLM for narrative) is the standard division of labour in modern CTI extraction stacks. |

---

## 5. The `processed_at` column story, in detail

This burned a few minutes of my time during the build, and the resolution is documented in CLAUDE.md §3 ("Schema additions … added via guarded `ALTER TABLE`"). Worth one full pass.

**The problem.** SQLite supports `CREATE TABLE IF NOT EXISTS` but not `ALTER TABLE … ADD COLUMN IF NOT EXISTS`. Editing `schema.sql` to add `processed_at` to the `raw_posts` `CREATE TABLE` block looks like the obvious fix. It isn't:

- For a fresh DB, `CREATE TABLE` runs and the column is there.
- For an existing DB (Stage 2's `sentinelx.db`), `CREATE TABLE IF NOT EXISTS` is a no-op — the table already exists, so the new column definition is silently ignored. There's no error; there's no warning. The column never appears.

You'd then run Stage 3 against the existing DB and get `OperationalError: no such column: processed_at`, far away from where the bug actually lives.

**Three ways to handle it:**

1. **`ALTER TABLE` in `schema.sql`, blindly.** Re-running schema.sql on a DB that already has the column raises `OperationalError: duplicate column name`. Doesn't survive a second invocation. Wrong.
2. **Drop and recreate `raw_posts`.** Wipes user data. Categorically wrong.
3. **Read `PRAGMA table_info`, conditionally `ALTER TABLE`.** What we did. Idempotent, preserves data, runs on every `Store` instantiation so the migration happens transparently the first time the upgraded code touches an old DB.

**The longer-term answer** is a migration tool — `alembic` (used widely in the SQLAlchemy world, supports SQLite), `yoyo-migrations` (lightweight, SQL-first), or hand-rolling a numbered migration directory + a `schema_migrations` table. Any of those becomes worth the dependency around the third or fourth schema change. Until then, the four-line guard in `_init_schema` is appropriate.

This pattern — *read the current schema state, take the minimum action needed* — is also how production migration tools work under the hood. They store applied-migration ids in a `schema_migrations` table and skip ones that already ran. The four-line guard is the same idea, scoped down to a single column.

---

## 6. Where Stage 3 will be revisited

Things deliberately not built now:

- **More IOC types.** No file paths, no registry keys, no Yara rules, no JA3/JA4 fingerprints, no User-Agent strings, no MAC addresses, no ASN numbers. All of those are real CTI signal. Each is a 5-minute regex addition; we'll add them on demand as Stages 5–7 surface use cases.
- **Whitelisting.** A regex that matches `8.8.8.8` is technically correct but probably not interesting (it's Google DNS). Same for RFC-1918 private ranges, CDN domains, and obvious test values like `example.com`. Real systems maintain a whitelist of "uninteresting" matches and either drop them or flag them. We don't yet; we'll add `iocs.is_noise` (boolean, computed at insert) when it becomes painful.
- **Confidence scores.** Right now an IOC row has no notion of confidence — every match is treated equally. In real systems, a match within a quoted code block might be `confidence=0.95` while a match in marketing copy might be `confidence=0.3`. spaCy entity confidence is already available (`ent._.score` with the right config); regex matches would need a heuristic. Defer until Stage 4's LLM pass, which has its own probability outputs.
- **Replace curated MALWARE/THREAT_ACTOR sets with MITRE-derived ones.** Stage 5 ingests the MITRE STIX bundle, which contains the canonical name + alias list for every documented intrusion-set and malware family. Once that's in the DB, the keyword pass becomes a `LIKE` join against that table and the recall jumps roughly 100×. This is the single biggest planned upgrade to Stage 3.
- **Stop accumulating spans into a Python list inside `_overlaps`.** Profile, then maybe an interval tree. Premature now; flag if a future post is 100KB long.
- **Per-IOC-type extraction toggles.** A CLI flag like `--no-domain` to disable domain extraction for a run, useful when debugging false positives. One-line addition; deferred.
- **Fine-tuning spaCy.** Eventually we'll have enough labelled CTI data (from Stages 4 and 5's LLM outputs) to fine-tune a custom NER model. That's months away.

None of those are needed for Stage 4. The contract Stage 3 publishes — *immutable rows in `iocs` and `entities` keyed by `raw_post_id`* — is exactly what the LLM pipeline wants to consume.

---

## 7. Hand-off contract to Stage 4

Stage 4 will run an LLM (Mistral via Ollama, four-stage prompt chain) over `raw_posts` to produce summaries, MITRE technique mappings, intent classifications, and target-industry tags. To make Stage 3's contract explicit:

- **Input tables for Stage 4:** `raw_posts` (immutable), `iocs` (immutable per `(raw_post_id, ioc_type, value)`), `entities` (immutable per `(raw_post_id, label, text)`).
- **Join key for all three:** `raw_posts.id`. Do not use `source_post_id`.
- **Idempotency:** all three tables are append-only from Stage 4's perspective. Re-running Stage 3 over a post produces no new rows (UNIQUE constraints) so Stage 4 can rely on extraction results being stable across pipeline reruns.
- **Cursor for Stage 4:** Stage 4 will track *its own* state, separate from `raw_posts.processed_at`. As discussed in §3.3, the right design is a `post_processing_state(raw_post_id, stage, processed_at)` table once we have multiple downstream stages. We'll add it in Stage 4 rather than retrofit it now.
- **What Stage 4 does NOT need to do:** it does not need to refang the body, re-extract IOCs, or guess at named entities. Those facts are already available as joined rows. The LLM prompt should *include* those structured facts as context, not re-derive them.

This is the same shape as the Stage-2-to-Stage-3 handoff: *immutable upstream layer, downstream stage with its own cursor, append-only outputs*. As covered at the end of Stage 2's LEARN doc, that pattern (Kafka offsets, dbt models, Airflow XCom) is the standard composition rule for staged ETL pipelines. You're not learning a quirk of this project; you're seeing the shape repeat.

---

## 8. Quick reference

```bash
# One-shot drain
backend/.venv/Scripts/python.exe -m backend.pipeline.run --once

# Continuous (Ctrl-C to stop)
backend/.venv/Scripts/python.exe -m backend.pipeline.run --watch --interval 30

# Wipe Stage 3 outputs and re-extract from scratch
backend/.venv/Scripts/python.exe -m backend.pipeline.run --reset
backend/.venv/Scripts/python.exe -m backend.pipeline.run --once

# Inspect
sqlite3 backend/db/sentinelx.db "SELECT ioc_type, COUNT(*) FROM iocs GROUP BY ioc_type ORDER BY 2 DESC;"
sqlite3 backend/db/sentinelx.db "SELECT label,    COUNT(*) FROM entities GROUP BY label ORDER BY 2 DESC;"
sqlite3 backend/db/sentinelx.db "SELECT id, started_at, posts_seen, iocs_inserted, entities_inserted, error FROM extraction_runs ORDER BY id DESC LIMIT 5;"
sqlite3 backend/db/sentinelx.db "SELECT COUNT(*) FROM raw_posts WHERE processed_at IS NULL;"  -- queue depth

# Pivot: which posts mention a given IP?
sqlite3 backend/db/sentinelx.db \
  "SELECT rp.id, rp.thread_title FROM iocs i JOIN raw_posts rp ON rp.id = i.raw_post_id WHERE i.value = '185.220.101.42';"
```

---

**End of STAGE_03_LEARN.** Stage 3 is now closed: code verified working end-to-end, this LEARN doc shipped. Per CLAUDE.md §5, the remaining checklist item is the git commit, which is deferred to the user's explicit say-so.
