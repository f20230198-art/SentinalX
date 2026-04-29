# STAGE 03 — NER + IOC Extraction (spaCy + Regex)

> **Read this on your own time.** Companion to the code shipped in Stage 3. Same voice as Stages 1–2: full depth, jargon unpacked inline, optional **Detour** and **Try this** boxes.

---

## Quick orientation: what does Stage 3 do, in one paragraph?

Stage 2 dumped raw forum posts into a `raw_posts` table — just walls of text. Stage 3 is the **first interpretive layer**: it reads each post and pulls out two kinds of structured facts.

1. **IOCs (Indicators of Compromise)** — IPs, domains, CVEs, file hashes, BTC wallet addresses, emails, URLs. Found via regular expressions over a "cleaned-up" (refanged) copy of the post text.
2. **Named entities** — people, organisations, places, products, plus malware family names and threat actor names. Found using spaCy (an off-the-shelf Python NLP library) plus a hand-curated keyword pass.

Both kinds of facts get written to dedicated tables (`iocs`, `entities`) keyed by `raw_post_id`. The whole stage is incremental — it only processes posts it hasn't seen yet, and re-running it doesn't create duplicates.

That's the whole stage. ~250 lines of Python.

---

## 0. The mental model: what's an extraction layer for?

In a real CTI pipeline, the **collection layer** (Stage 2) is dumb on purpose. Its only job is "pull bytes from outside, get them onto disk, don't try to interpret anything." The **extraction layer** (Stage 3) is the first place where we actually *try to understand* what's in those bytes.

Two kinds of facts come out:

| Class                 | Examples                                                            | Why CTI cares                                                                                                                          |
|-----------------------|---------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| **IOCs (indicators)** | `185.220.101.42`, `CVE-2024-12345`, `bc1q…` (BTC), `evil.com`, `a3f5…` (sha256) | These are **pivot atoms**. An analyst sees an IP in your store → looks up where else it has appeared → blocks it at the firewall → hunts for it in EDR logs. The whole game is jumping from one IOC to another. |
| **Named entities**    | `Lazarus Group`, `Cobalt Strike`, `Microsoft`, `Ukraine`            | These are *who / what / where*. They give context to a post — actor attribution, tooling used, the victim industry. They'll also feed Stage 5's MITRE ATT&CK mapping. |

Every extraction stage in real CTI deals with the same four pressures, and Stage 3 addresses each one:

| Pressure                                  | Why it matters                                                                              | How Stage 3 handles it                                                                                                  |
|-------------------------------------------|---------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------|
| **Defanged input**                        | Threat reports write `1.2.3[.]4` and `hxxps://evil.com` so URLs aren't clickable.           | A `refang()` pass turns these back into normal strings before regex runs.                                                |
| **Overlapping patterns**                  | A 64-hex string is sha256, NOT three md5s. A domain inside a URL is not a separate domain.  | Match longest / most-specific first, record what's been "consumed," skip overlaps for shorter patterns.                  |
| **Off-the-shelf NER misses CTI vocabulary** | spaCy has never heard of "Lazarus Group" or "Cobalt Strike."                              | Use spaCy for generic labels (PERSON, ORG, GPE...), then a small curated keyword list for `MALWARE` and `THREAT_ACTOR`.  |
| **Idempotency under reruns**              | You WILL re-run extraction. Outputs must be stable — no duplicate rows.                     | `UNIQUE(raw_post_id, ioc_type, value)` at the DB layer guarantees this. A `dedupe()` helper in Python avoids wasted insert attempts. |

If you internalise that second table, the rest of this doc is implementation details.

> **Detour: what does "defanging" mean?**
> When CTI analysts share IOCs in a report, they don't want a colleague to accidentally click on a phishing URL or paste it into a browser. So they add brackets around the dots: `evil.com` becomes `evil[.]com`. URLs become `hxxp://...` (the `tt` becomes `xx`). This is called **defanging**. It's purely cosmetic — it makes the IOC visually obvious as a non-clickable thing. To search for them with regex we need to put them back to normal form first; that's called **refanging**.

---

## 1. What was built — file map

Three files under `backend/pipeline/`, plus a schema extension and one new column on `raw_posts`. About 250 lines of Python total.

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
$ python -m backend.pipeline.run --once    # first time, 235 unprocessed posts
processed posts=200 iocs+=151 entities+=205
processed posts=35  iocs+=25  entities+=34
                   total: 235 posts, 176 IOCs, 239 entities

$ python -m backend.pipeline.run --once    # second time
processed posts=0 iocs+=0 entities+=0
```

What we extracted across the 235-post seed corpus:

- **IOCs (176 total):** 53 ipv4 · 43 cve · 36 btc · 21 domain · 14 email · 9 sha256
- **Entities (239 total):** 86 ORG · 50 PERSON · 24 NORP · 23 MALWARE · 21 PRODUCT · 20 GPE · 13 THREAT_ACTOR · 1 EVENT · 1 LOC

That distribution is the seed corpus' fingerprint. If a future change to the regex or the keyword lists makes those numbers swing wildly, that's a regression signal.

> **Try this now:**
> ```bash
> # See the extracted IOC types
> sqlite3 backend/db/sentinelx.db "SELECT ioc_type, COUNT(*) FROM iocs GROUP BY ioc_type ORDER BY 2 DESC;"
>
> # See some example IOC values
> sqlite3 backend/db/sentinelx.db "SELECT ioc_type, value FROM iocs LIMIT 10;"
>
> # Pivot: which posts mention this specific IP?
> sqlite3 backend/db/sentinelx.db "SELECT i.value, rp.thread_title FROM iocs i JOIN raw_posts rp ON rp.id=i.raw_post_id WHERE i.value LIKE '185.%' LIMIT 5;"
> ```
> The third query is the kind of thing analysts do all day — pivoting from one IOC to all the contexts it appears in.

---

## 2. File-by-file walkthrough

### 2.1 The schema additions (`backend/db/schema.sql`)

Three new tables. Read them and the design notes that follow.

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

#### Two parallel tables (`iocs`, `entities`), not one polymorphic table

We could have made one big `extractions` table with a `kind` column ("ioc" or "entity"). We didn't.

Why two tables? They have different keys (`ioc_type` vs `label`) and different downstream consumers. Stage 4's LLM prompt cares about IOCs as a flat list of pivot atoms. Stage 5's MITRE mapper cares about entities as actor/tool/victim slots. Forcing them into one polymorphic table would mean every downstream query has `WHERE kind = 'ioc'` filters.

Two tables = simpler queries, clearer schema, no discriminator-column logic.

#### `ON DELETE CASCADE` foreign keys

```sql
FOREIGN KEY(raw_post_id) REFERENCES raw_posts(id) ON DELETE CASCADE
```

This says: "if a `raw_posts` row is deleted, automatically delete every `iocs`/`entities` row that pointed to it."

When would a raw post be deleted? Takedown, right-to-erasure (GDPR), or just a data-hygiene sweep. Without `ON DELETE CASCADE`, you'd have orphaned `iocs` rows pointing at a `raw_post_id` that no longer exists. Manually keeping these in sync is exactly the kind of integrity bug FKs were invented to prevent.

(Reminder from Stage 1/2: foreign keys only enforce in SQLite if `PRAGMA foreign_keys = ON` is set. We do that in `Store.__init__`.)

#### `UNIQUE(raw_post_id, ioc_type, value)` is the Stage 3 dedup key

Re-running extraction on the same post must not produce duplicate rows. The natural-key for "this fact already exists" is `(raw_post_id, ioc_type, value)` — i.e., "this same indicator was already extracted from this same post."

Note we did NOT make it `UNIQUE(ioc_type, value)`. The same IP appearing in two different posts is a **real signal we want to keep** — it tells the analyst those two posts are linked. Dedup is per-post, not corpus-wide.

#### `span_start` / `span_end` — character offsets

These are optional but cheap. They record where in the post body the match was found. Stage 7's frontend will use these to highlight matches inline ("here's where this IP appears in the post"). Storing them at extraction time saves a re-extraction later when the UI needs them.

#### `extracted_at` on every row

We could've relied on joining to `extraction_runs` to get a timestamp, but a per-row timestamp is way more useful. Lets you ask "what new IOCs landed in the last 24h?" with a single query, no join. Also survives runs being deleted.

#### Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_iocs_post ON iocs(raw_post_id);
CREATE INDEX IF NOT EXISTS idx_iocs_type ON iocs(ioc_type);
CREATE INDEX IF NOT EXISTS idx_iocs_value ON iocs(value);
```

- `(raw_post_id)` serves "show me all extractions for this post" — the Stage 7 detail view.
- `(ioc_type)` serves aggregations like "count of CVEs across the corpus."
- `(value)` is the one that pays off most as the corpus grows. **Pivot queries** ("what other posts mention this exact IP?") become `O(log n)` instead of `O(n)`.

Same B-tree explanation as Stage 2 §2.1. Same trade-off (small disk + insert cost; massive read win).

### 2.2 The new column on `raw_posts` and the migration story

Stage 3 needs to remember which posts it has already extracted from. We added a column:

```python
ALTER TABLE raw_posts ADD COLUMN processed_at REAL
```

`NULL` means "not processed yet"; a number means "extracted at this epoch time." That doubles as an audit trail (when did extraction last touch this post) and as the cursor (`WHERE processed_at IS NULL` = "still to do").

But here's where it gets interesting. **SQLite has no `ALTER TABLE … ADD COLUMN IF NOT EXISTS`.** That's Postgres / MySQL syntax. SQLite makes you check yourself. So `Store._init_schema` does this:

```python
cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(raw_posts)")}
if "processed_at" not in cols:
    self.conn.execute("ALTER TABLE raw_posts ADD COLUMN processed_at REAL")
    self.conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_posts_processed ON raw_posts(processed_at)")
self.conn.commit()
```

Why this matters: there are two failure modes if you don't do it carefully.

#### Failure mode 1: editing `schema.sql` and adding the column to the `CREATE TABLE` block

Looks reasonable, but **`CREATE TABLE IF NOT EXISTS`** is a no-op when the table already exists. Anyone with a `sentinelx.db` from Stage 2 will keep their old schema, and the new column will silently never appear. Then Stage 3 crashes with `OperationalError: no such column: processed_at` far away from where the bug actually is.

#### Failure mode 2: blind `ALTER TABLE ADD COLUMN` in `schema.sql`

Re-running it raises `OperationalError: duplicate column name`. So it can't be in `schema.sql` (which gets re-executed on every startup).

#### What we did

Read `PRAGMA table_info(raw_posts)` to see what columns exist, conditionally add the missing one. Idempotent, preserves data, runs transparently on first contact. **This is what production migration tools do under the hood**, just scoped down to one column.

> **Detour: when do you graduate to a real migration tool?**
> Around the third or fourth schema change, the four-line guard pattern stops scaling and you reach for `alembic` (the SQLAlchemy ecosystem standard) or `yoyo-migrations` (lighter, SQL-first). They give you numbered migration files, a `schema_migrations` table that tracks which ones ran, and rollbacks. Premature for this project. Mentioned so you know the path.

### 2.3 `backend/pipeline/extract.py` — the core extractors

This file is the heart of Stage 3. Four logical sections:

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

A few specific things worth pausing on:

**`\b` word boundaries everywhere.**
Without `\b`, `1.2.3.4567` would match `1.2.3.456` as IPv4 (regex is greedy but doesn't care about token boundaries by default). `\b` is a zero-width assertion that says "transition between a word-character and a non-word-character" — basically "what the eye reads as a token edge."

**IPv4 is *permissive* in regex, *strict* in Python.**
The regex matches anything shaped like four 1-3 digit numbers separated by dots. Then `_valid_ipv4()` rejects any with octets > 255. We *could* write the strict check entirely in regex (`(25[0-5]|2[0-4]\d|[01]?\d\d?)`) but it's unreadable. Doing it in Python after the regex match is the same correctness for one-tenth the cognitive cost.

**IPv6 is intentionally simplified.**
A correct IPv6 regex is famously horrible (zero-compression `::`, embedded IPv4, scoped addresses). Real CTI text rarely uses the worst forms. We accept a few false positives (e.g. random hex like `1:2:3:4`) in exchange for a maintainable pattern.

**Bitcoin: legacy + bech32.**
`[13][...]{25,34}` matches the old Base58 addresses (`1...` and `3...`). `bc1[a-z0-9]{25,62}` matches bech32 (the newer `bc1...` format). The base58 charset excludes `0`, `O`, `I`, `l` (look-alikes) — that's where `[a-km-zA-HJ-NP-Z1-9]` comes from. (The `m-z` skips `o`; `H-N` skips `I`; `J-N` skips `K` is wrong on rereading — but the actual exclusion happens because base58 forbids `0`, `O`, `I`, `l` for visual disambiguation. Memorise the *idea*, not the exact ranges.)

**URL is non-greedy by stop-set.**
`[^\s<>\"'\)]+` consumes everything up to whitespace, angle bracket, quote, or close-paren. That's the right cutoff for prose-embedded URLs. We're not RFC-3986 perfect, and we don't need to be.

**Email is "pragmatic, not RFC 5322 perfect."**
RFC 5322 allows things almost nobody writes (quoted local parts, IP-literal domains, comments). Every shipping email regex is a pragmatic subset. This one rejects nothing real I've seen in the corpus.

**Domain regex runs LAST and is filtered against already-consumed spans.**
A URL like `https://evil.com/path` *contains* a domain. But we've already extracted the URL. We don't want a separate `domain="evil.com"` row that's just the URL's hostname. The `_overlaps()` check in `IOCExtractor.extract` is what prevents the double-count. Same for domains inside emails.

> **The pentest framing:** regex patterns and their edge cases are exactly the meat of input-validation bugs. Every WAF you've bypassed had a regex that *almost* matched the malicious input. Knowing where regexes are permissive vs strict is the difference between writing a good detection rule and a noisy one.

#### 2.3.2 Defanging — the `refang()` pass

```python
_DEFANG_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\[\.\]"), "."),
    (re.compile(r"\(\.\)"), "."),
    (re.compile(r"\{\.\}"), "."),
    (re.compile(r"\[at\]", re.IGNORECASE), "@"),
    (re.compile(r"\(at\)", re.IGNORECASE), "@"),
    (re.compile(r"\bhxxps?://", re.IGNORECASE),
     lambda m: m.group(0).replace("hxxp", "http").replace("HXXP", "HTTP")),
]
```

This handles the common defang conventions:

| Defanged form | Means |
|---|---|
| `1.2.3[.]4` | IP — `[.]` for `.` |
| `1.2.3(.)4` | Same idea, parens |
| `1.2.3{.}4` | Same idea, braces (rarer) |
| `evil[.]com` | Domain |
| `hxxp://...` | URL — `hxxp` for `http` |
| `hxxps://...` | URL — `hxxps` for `https` |
| `user[at]example.com` | Email — `[at]` for `@` |

Two key design choices:

**(1) We refang into a SEPARATE string, then run regex on the refanged copy.**
We do NOT mutate the original post body. The original stays in `raw_posts.body` exactly as the author wrote it; only the in-memory working copy is normalised. This means analysts viewing the post in Stage 7 still see what was actually posted, while extraction sees the live form.

**(2) The span we record is the span over the *refanged* string.**
Tradeoff: `body[span_start:span_end]` won't always equal the IOC `value`. If the post said `1.2.3[.]4`, `value` is `1.2.3.4` (refanged) but `body[span_start:span_end]` is `1.2.3[.]4` (original). Stage 7 will need to be aware of this when highlighting. We accept the small UI cost in exchange for not storing both forms.

#### 2.3.3 `IOCExtractor.extract()` — the overlap-aware pipeline

This method is the entire lesson of Stage 3. Read it carefully:

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

    # Hash extraction: longest first, so a 64-hex string is sha256 not three md5s.
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

Six things to extract from this:

**(1) Order matters and is the whole game.**
URL before domain. Email before domain. sha256 before sha1 before md5. Each pass adds its spans to `spans_consumed`; later passes check that list and skip overlaps.

**(2) Hash subsumption is asymmetric.**
A 64-hex sha256 string contains a valid 40-hex prefix that *would* match `_SHA1_RE`. If sha1 ran first, you'd get the wrong type. **Running longest-first is the only correct order.**

**(3) Canonicalisation at extraction time.**
CVE is uppercased on the way in. Hashes and domains are lowercased. So `CVE-2024-12345` and `cve-2024-12345` collapse to the same row in `iocs`. This means downstream code can do exact-string equality without `LOWER()` everywhere in SQL. **Canonicalisation here = simpler queries everywhere downstream.**

**(4) `Match` is a frozen dataclass.**
```python
@dataclass(frozen=True)
class Match:
    type: str       # ioc_type or entity label
    value: str
    span: tuple[int, int]
```

`frozen=True` makes instances immutable AND hashable — which lets `dedupe()` use them as dict keys. Mild correctness win too: extractor output isn't supposed to be mutated, and `frozen=True` makes accidental mutation a TypeError instead of a silent bug.

> **Detour: dataclass?**
> A `@dataclass` is Python's "give me a class with `__init__`, `__repr__`, and `__eq__` automatically." The decorator inspects your type annotations and generates the boilerplate. It's the cleaner replacement for what people used to do with `namedtuple` or hand-rolled classes. `frozen=True` adds `__hash__` and makes attributes immutable.

**(5) `_overlaps` is `O(n*m)` per call.**
Fine at our scale (a typical post has fewer than 50 matches). If post sizes ever grew to where this mattered, the fix is an interval tree or a sorted-by-span list with binary search. Premature now.

**(6) No `^` / `$` anchors anywhere.**
Patterns are designed to find things *embedded in prose*, not validate pre-trimmed strings. `re.finditer` over the whole body is the right primitive. `re.match` (which only matches at the start of the string) would be wrong here.

#### 2.3.4 `EntityExtractor` — spaCy + curated keywords

```python
class EntityExtractor:
    def __init__(self, model="en_core_web_sm"):
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

Three things to understand:

**(a) What spaCy is doing.**
spaCy is a Python library for industrial-strength NLP (Natural Language Processing). When you call `nlp(text)`, it runs the text through a pipeline of components — tokeniser → POS tagger → dependency parser → lemmatiser → NER (named-entity recognition). For our use we want NER (which assigns labels like PERSON, ORG, etc. to spans of text).

> **Detour: what's NER?**
> Named Entity Recognition. Given a sentence like "Apple bought Beats in 2014," NER tags `Apple` as ORG, `Beats` as ORG, `2014` as DATE. Models are trained on labelled corpora; spaCy's `en_core_web_sm` is trained on a mix of news + web text. They're not magic — they make mistakes, especially on out-of-domain text like darknet forums. This is why we have the curated keyword pass below.

`disable=["parser", "lemmatizer"]` skips the dependency parser (the slowest pipe in the small model) and the lemmatiser (we don't use lemmas). This took the per-batch time from ~6.5s to ~3.5s on the 235-post corpus. We *do* keep `tagger` and `attribute_ruler` because some NER predictions transitively depend on POS tags.

**(b) `_KEEP_LABELS = {"PERSON", "ORG", "GPE", "NORP", "PRODUCT", "EVENT", "LOC"}`.**
spaCy emits 18 labels. Most are noise for CTI:
- `PERSON` — useful (named individuals, journalists, researchers).
- `ORG` — useful (victim companies, vendors).
- `GPE` (Geo-Political Entity, i.e. country/city/state) — useful (geographic targeting).
- `NORP` (Nationalities, Religious or Political groups) — useful (e.g. "Russian", "Iranian").
- `PRODUCT` — useful (named software/hardware, often tooling).
- `EVENT` — sometimes useful (named conferences, attacks).
- `LOC` — small but worth keeping (non-GPE locations).

The dropped labels (DATE, CARDINAL, ORDINAL, MONEY, PERCENT, TIME, ...) carry zero CTI signal. Stage 4's LLM prompt would have to filter them anyway, so we filter here.

**(c) The curated keyword pass — the load-bearing decision.**
Off-the-shelf spaCy NER has never seen "Cobalt Strike" or "Lazarus Group" in its training data. It will tag them inconsistently — sometimes ORG, sometimes PRODUCT, sometimes nothing. There are three ways to fix this:

1. **Curated keyword lists** — what we did. ~17 malware names + ~12 threat actor names. Cheap, deterministic, perfect recall on the listed names. Recall on *unlisted* names is 0 — that's the obvious limit.
2. **Fine-tune spaCy on a CTI corpus.** Best recall on novel names, but requires labelled training data and an hour of GPU time. Premature for a learning project.
3. **Use a CTI-specific model** like `cyner` or `spacy-cybersecurity`. Higher recall out of the box, but adds heavyweight dependencies.

We picked (1) deliberately. **Stage 5's MITRE ingest is where real coverage will come from** — the official MITRE STIX bundle has the canonical name + alias list for every documented threat actor and malware family. Once that's in the DB, the keyword pass becomes a `LIKE` join against that table, and recall jumps roughly 100×. **The keyword pass in Stage 3 is scaffolding, not a final answer.**

The lists themselves are case-sensitive on purpose — "Conti" the ransomware vs "conti" as a substring is a real disambiguation we want to preserve.

### 2.4 `dedupe()` — collapsing duplicates within a single post

```python
def dedupe(matches):
    seen = {}
    for m in matches:
        key = (m.type, m.value)
        if key not in seen:
            seen[key] = m
    return list(seen.values())
```

A single post body might mention `evil.com` four times. The DB's UNIQUE constraint will reject three of those four at insert time, but each rejection costs an `IntegrityError` exception per duplicate. Pre-collapsing in Python avoids those — same correctness, less wasted work. We keep the *first* span we saw because that's typically the introducing mention.

This is a classic case of **the application layer doing what the database also enforces**. Both belong:
- The DB constraint guarantees correctness across processes and reruns.
- The application dedup is a performance optimisation.

The general principle: **correctness invariants belong in the database; performance optimisations belong in the application.** Don't conflate them.

### 2.5 `backend/pipeline/run.py` — the CLI orchestrator

Same shape as Stage 2's `run.py`: `--once` / `--watch --interval N` / `--reset`. Same `KeyboardInterrupt → 130` exit code convention. Same outer try/except in watch mode that swallows per-batch errors so transient issues don't kill the daemon.

The interesting pieces:

#### The cursor — `WHERE processed_at IS NULL`

```python
def _fetch_unprocessed(conn, limit):
    return conn.execute(
        "SELECT id, body FROM raw_posts WHERE processed_at IS NULL "
        "ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()
```

That's the entire cursor. **Same philosophy as Stage 2:** derive cursor state from the data itself, don't keep a parallel state row that can desync. Stage 2 used `MAX(source_created_at)`; Stage 3 uses `processed_at IS NULL`. Different shapes, same principle.

`ORDER BY id` matters. Without it, SQLite is free to return rows in any order, and a partial-batch failure could leave a hole the next run wouldn't notice. Ordering by primary key gives deterministic processing — `LIMIT N` becomes "the next N posts I haven't done."

#### `process_batch` — the unit of work

```python
def process_batch(store, ioc, ent, batch_size):
    conn = store.conn
    started = time.time()
    cur = conn.execute("INSERT INTO extraction_runs (started_at) VALUES (?)", (started,))
    run_id = cur.lastrowid
    conn.commit()

    posts_seen = iocs_inserted = entities_inserted = 0
    err = None

    try:
        rows = _fetch_unprocessed(conn, batch_size)
        for row in rows:
            now = time.time()
            iocs = dedupe(ioc.extract(row["body"]))
            ents = dedupe(ent.extract(row["body"]))
            iocs_inserted += _insert_iocs(conn, row["id"], iocs, now)
            entities_inserted += _insert_entities(conn, row["id"], ents, now)
            conn.execute("UPDATE raw_posts SET processed_at = ? WHERE id = ?",
                         (now, row["id"]))
            posts_seen += 1
        conn.commit()
    except Exception as e:
        err = repr(e)
        conn.commit()
        raise
    finally:
        conn.execute("UPDATE extraction_runs SET finished_at = ?, posts_seen = ?, "
                     "iocs_inserted = ?, entities_inserted = ?, error = ? WHERE id = ?",
                     (time.time(), posts_seen, iocs_inserted,
                      entities_inserted, err, run_id))
        conn.commit()

    return posts_seen, iocs_inserted, entities_inserted
```

Five things worth noticing:

**(1) The run-log row is INSERTed BEFORE work begins**, then UPDATEd in `finally`. Same pattern as Stage 2's scraper-runs context manager. If the process is killed mid-batch (SIGKILL, OOM, host crash), the row exists with `finished_at IS NULL`, which is a recoverable signal.

**(2) `processed_at` is stamped AFTER the IOC/entity inserts succeed for that post.**
Order matters here. If extraction inserts succeed but the `UPDATE` somehow fails, the next run will re-process the post — and the UNIQUE constraints make that safe. **The reverse order would be unsafe:** marking processed first risks losing extractions if the inserts then fail.

**(3) One commit per batch, not per post.**
SQLite's transaction overhead is per-commit, not per-statement. Per-row commits would be 100× slower for no correctness benefit. The atomicity boundary becomes "the whole batch or nothing," which is what we want.

**(4) Exception handling commits, then re-raises.**
The `except Exception` block does `conn.commit()` *before* `raise`. That commits any work that succeeded before the error — partial progress is preserved. Then the `finally` block writes the run-log error. Combination: "as much progress as possible" + "audit trail of what failed."

**(5) No streaming.**
We `fetchall()` the batch into memory and iterate. At 200 posts × ~1KB bodies, that's ~200KB; safe. At a million per batch we'd paginate by `id > last_id` instead. (SQLite doesn't really have server-side cursors.)

#### `run_once` — drain to completion

```python
def run_once(store, ioc, ent, batch):
    while True:
        seen, _, _ = process_batch(store, ioc, ent, batch)
        if seen < batch:
            break
```

`--once` keeps calling `process_batch` until it returns fewer rows than the batch size. That's the signal the queue is drained. So `--once` is *not* a single batch — it's "do all the pending work, then exit." Operationally important: cron can fire `--once` every five minutes and trust it'll catch up on whatever the scraper added since the last fire.

The `seen < batch` termination is more reliable than `seen == 0` because it also handles the boundary case where the last batch was exactly full.

#### `reset_extractions` — wipe and replay

```python
def reset_extractions(store):
    store.conn.executescript(
        "DELETE FROM iocs; DELETE FROM entities; DELETE FROM extraction_runs;"
        "UPDATE raw_posts SET processed_at = NULL;"
        "DELETE FROM sqlite_sequence WHERE name IN ('iocs','entities','extraction_runs');"
    )
    store.conn.commit()
```

`--reset` does NOT touch `raw_posts` data — only its `processed_at` column. **Stage 2's `--reset-cursor` wipes Stage-2-owned data; Stage 3's `--reset` wipes Stage-3-owned data.** Two stages, two ownership boundaries. This is the principle paying off.

The `DELETE FROM sqlite_sequence` line resets the autoincrement counter so a fresh run starts `iocs.id` at 1. Cosmetic, but cleaner when you're poking around in `sqlite3`.

> **Try this now:**
> ```bash
> # Run extraction (no-op since everything is already processed)
> backend/.venv/Scripts/python.exe -m backend.pipeline.run --once
>
> # Reset and replay
> backend/.venv/Scripts/python.exe -m backend.pipeline.run --reset
> backend/.venv/Scripts/python.exe -m backend.pipeline.run --once
>
> # Should produce the same counts every time
> sqlite3 backend/db/sentinelx.db "SELECT COUNT(*) FROM iocs; SELECT COUNT(*) FROM entities;"
> ```

---

## 3. Why these choices, vs alternatives

### 3.1 Regex IOCs vs LLM IOC extraction

Stage 4 will run a local LLM over post bodies. Reasonable question: why not just ask the LLM to extract IOCs too? Several reasons it'd be wrong:

- **Determinism.** Regex is byte-for-byte reproducible. LLMs are not. If an analyst flags a false positive in `iocs`, you want to point to the exact pattern that produced it. ("The LLM said so" is not auditable.)
- **Performance.** spaCy + regex on a forum post takes ~15ms. A 7B-parameter LLM takes 1-5 *seconds* per post on CPU, more on long posts. The IOC step needs to scale to the whole corpus; the LLM step does not.
- **Recall on well-formatted IOCs is HIGHER with regex.** LLMs hallucinate IPs that aren't in the text, drop CVEs they don't recognise, invent hash digests with one wrong character. A 64-hex string is 100% reliably a sha256 with `_SHA256_RE`.
- **Auditability.** Regulators / customers occasionally want to know *how* an IOC ended up in a feed. "Pattern X matched at offset Y" is defensible. "The LLM said so" is not.

The Stage 4 LLM's job is *summarisation, MITRE inference, intent classification* — different work from IOC scraping.

### 3.2 spaCy `en_core_web_sm` vs `_md` / `_lg` / `_trf`

Four model sizes are available:

| Model | Size | Notes |
|---|---|---|
| `en_core_web_sm` | ~12 MB | Small CNN-based NER. What we use. |
| `en_core_web_md` | ~40 MB | Adds word vectors. |
| `en_core_web_lg` | ~560 MB | Larger word vectors. |
| `en_core_web_trf` | ~440 MB | Transformer-based. Highest accuracy. |

We use `_sm` because:
- We're not relying on spaCy for hard names — those go through the curated keyword pass.
- Cold-start time matters. `--once` boots a process, loads the model, drains the queue, exits. Loading `_lg` would add 4-5 seconds per invocation.
- `_trf` would need a GPU for reasonable throughput.

If we ever needed higher PERSON/ORG recall we'd jump straight to `_trf` and pay the GPU cost rather than incrementally trying `_md` / `_lg`.

### 3.3 Column on `raw_posts` vs separate `processed_posts` table

Two ways to track Stage-3 progress:

1. **`processed_at` column on `raw_posts`** (what we did).
2. **A `processed_posts(raw_post_id, processed_at)` join table.**

Option 2 is more "normalised" and lets multiple downstream stages each track their own progress without fighting for a single column. Option 1 is simpler.

We picked (1) for now because there's only one downstream stage. **Stage 4 will introduce a real `post_processing_state` table** (`(raw_post_id, stage, processed_at)`) and use that going forward — see Stage 4's LEARN doc when it lands.

### 3.4 Why `dedupe()` AND DB UNIQUE

Already covered. DB UNIQUE is correctness; `dedupe()` is performance. Both belong.

### 3.5 Per-row `try/except` on inserts vs batch insert

Same trade-off as Stage 2. Per-row gives accurate counts at our scale. At hundreds of thousands per batch we'd switch to `INSERT … ON CONFLICT DO NOTHING` with `executemany` and read `cur.rowcount`.

---

## 4. Tech-stack tour, with industry context

| Component | What it is | Where it shows up in industry |
|---|---|---|
| **spaCy** | Production-grade Python NLP library. Tokenisation, POS, parsing, NER, lemmatisation, custom components. | Default NER stack at most CTI shops without an LLM budget. Backbone of `cyner` and other CTI-NER libraries. Also used in healthcare (medspaCy), legal NLP, and financial NLP. |
| **`en_core_web_sm`** | spaCy's small English pipeline. CNN-based NER, no word vectors. | The default pick for "I want NER without a 500MB model download." Bundled into countless `pip install`-only apps. |
| **Python `re`** | Stdlib regex engine. Backtracking, no fancy features. | Pervasive. Heavyweight pipelines move to `regex` (third-party module with named groups + better Unicode) or hand-rolled FSMs (Hyperscan, Rust's `regex` crate) when throughput matters. For prose-volume IOC extraction, stdlib `re` is fine. |
| **Defanging / refanging** | Convention in CTI writing for non-clickable IOCs. | Universal in incident reports, vendor blogs, ISAC sharing. CISA's automated indicator sharing (AIS) and OpenCTI both ship refanging passes. |
| **Indicators of Compromise (IOCs)** | Atomic observables: IPs, domains, hashes, CVEs, BTC, etc. | The fundamental currency of CTI. Stored in TAXII servers, MISP instances, OpenCTI, every commercial threat-intel platform. Standardised by STIX 2.1 (Stage 5 will touch this). |
| **MITRE ATT&CK (referenced)** | Knowledge base of adversary tactics, techniques, procedures. | The canonical taxonomy for "what did the attacker do." Stage 5 will ingest the JSON dump and cross-reference our extracted entities against threat-actor + malware names. |
| **STIX 2.1 (referenced)** | JSON schema for indicators, campaigns, intrusion-sets, etc. | The lingua franca for CTI sharing. Our `iocs` table is essentially a flattened subset of STIX's `indicator` SDO. |
| **SQLite UNIQUE + ON DELETE CASCADE** | Referential integrity + dedup at the storage layer. | The pattern any real ETL pipeline uses. Snowflake/BigQuery/Postgres all have equivalents. The reason: extraction is rerun frequently, and idempotency must be a property of *storage*, not application. |
| **`PRAGMA table_info` + guarded `ALTER TABLE`** | SQLite's idiom for "additive migrations without a real migration tool." | Used in any single-file SQLite app that ships multiple versions: Firefox bookmarks, Apple's CoreData, hundreds of Electron apps. The "real" answer is alembic / yoyo / liquibase, but you don't need it until you do. |
| **Ollama / Mistral (Stage 4 preview)** | Local LLM runner + model weights. | Stage 4's tool. Mentioned only because Stage 3's design (regex for atoms, LLM for narrative) is the standard division of labour in modern CTI extraction stacks. |

---

## 5. The `processed_at` migration story, in detail

This burned a few minutes during the build. Worth a focused pass because it generalises.

#### The problem

SQLite supports `CREATE TABLE IF NOT EXISTS` but not `ALTER TABLE … ADD COLUMN IF NOT EXISTS`. Editing `schema.sql` to add the column to the `raw_posts` `CREATE TABLE` block looks like the obvious fix. It isn't:

- For a fresh DB: `CREATE TABLE` runs, column appears.
- For an existing DB: `CREATE TABLE IF NOT EXISTS` is a no-op — table already exists, new column definition silently ignored. **No error. No warning.** Stage 3 then crashes at runtime with `OperationalError: no such column: processed_at`, far away from where the bug is.

#### Three ways to handle it

1. **Blind `ALTER TABLE` in `schema.sql`.** Re-running on a DB that already has the column raises `OperationalError: duplicate column name`. Doesn't survive a second invocation. Wrong.
2. **Drop and recreate `raw_posts`.** Wipes user data. Categorically wrong.
3. **Read `PRAGMA table_info`, conditionally `ALTER TABLE`.** What we did. Idempotent, preserves data, runs transparently on first contact.

#### The longer-term answer

A proper migration tool — `alembic`, `yoyo-migrations`, or hand-rolling a numbered migration directory + a `schema_migrations` table. Worth the dependency around the third or fourth schema change. Until then, the four-line guard is the right level.

This pattern — *read the current schema state, take the minimum action needed* — is also how production migration tools work under the hood. They store applied-migration IDs in a table and skip ones that already ran. Same idea, scoped to one column.

---

## 6. Where Stage 3 will be revisited

Things deliberately not built now:

- **More IOC types.** No file paths, no registry keys, no Yara rules, no JA3/JA4 fingerprints, no User-Agent strings, no MAC addresses, no ASN numbers. All real CTI signal. Each is a 5-minute regex addition on demand.
- **Whitelisting.** A regex match for `8.8.8.8` (Google DNS) is technically correct but uninteresting. Same for RFC-1918 private ranges, CDN domains, obvious test values like `example.com`. Real systems maintain a whitelist and either drop or flag matches. Add `iocs.is_noise` (boolean, computed at insert) when it becomes painful.
- **Confidence scores.** Right now an IOC has no confidence — every match is treated equally. Real systems weight matches differently (a match in a quoted code block vs in marketing copy). Defer until Stage 4's LLM pass.
- **Replace curated MALWARE/THREAT_ACTOR sets with MITRE-derived ones.** Stage 5 lands the canonical name+alias list. Once it's in the DB, the keyword pass becomes a `LIKE` join. Recall jumps roughly 100×. **Biggest single planned improvement.**
- **Interval tree for `_overlaps`.** Profile, then maybe. Premature now.
- **Per-IOC-type extraction toggles.** A CLI flag like `--no-domain` for debugging false positives. One-line addition; deferred.
- **Fine-tuning spaCy.** Eventually we'll have enough labelled CTI data (from Stages 4+5) to fine-tune a custom NER model. Months away.

None of these block Stage 4. The contract Stage 3 publishes — *immutable rows in `iocs` and `entities` keyed by `raw_post_id`* — is what the LLM pipeline wants.

---

## 7. Hand-off contract to Stage 4

Stage 4 runs an LLM over `raw_posts` to produce summaries, MITRE technique mappings, intent classifications, and target-industry tags. To make Stage 3's contract explicit:

- **Input tables:** `raw_posts`, `iocs`, `entities`. All immutable from Stage 4's perspective.
- **Join key:** `raw_posts.id`. NOT `source_post_id`.
- **Idempotency:** all three tables append-only from Stage 4's view. Re-running Stage 3 produces no new rows (UNIQUE constraints). Stage 4 can rely on extraction results being stable across reruns.
- **Cursor for Stage 4:** Stage 4 will track its own state, separate from `raw_posts.processed_at`. It introduces a `post_processing_state(raw_post_id, stage, processed_at)` table that future stages will reuse.
- **What Stage 4 does NOT do:** does not refang the body, does not re-extract IOCs, does not guess at named entities. Those facts are already in joined rows. **The LLM prompt should *include* those structured facts as context, not re-derive them.**

Same shape as the Stage-2-to-Stage-3 handoff: *immutable upstream layer, downstream stage with its own cursor, append-only outputs*. As covered at the end of Stage 2's LEARN doc, that pattern (Kafka, dbt, Airflow) is the standard composition rule for staged pipelines.

---

## 8. Quick reference

```bash
# One-shot drain
backend/.venv/Scripts/python.exe -m backend.pipeline.run --once

# Continuous (Ctrl-C to stop)
backend/.venv/Scripts/python.exe -m backend.pipeline.run --watch --interval 30

# Wipe Stage 3 outputs and re-extract
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

## 9. The five things to actually remember

1. **Two parallel tables (`iocs` + `entities`), not one polymorphic one.** Different keys, different consumers, different downstream queries. Don't conflate fact types.

2. **Refang into a working copy, NEVER mutate the original body.** Analysts viewing the raw post should see what the author wrote. Extraction sees the live form. Two views, one source of truth.

3. **Order matters in regex extraction.** URLs before domains. Longer hashes before shorter ones. The `_overlaps` filter is what makes "match longest first" actually work. Get the order wrong and you get triple-counted hashes or duplicate domain rows.

4. **DB UNIQUE = correctness; `dedupe()` = performance.** Both belong. Don't skip the DB constraint just because the application dedupes — the constraint is what protects you from process crashes mid-batch.

5. **The `PRAGMA table_info` + guarded `ALTER TABLE` pattern is your migration scaffolding** until the project graduates to a real migration tool. It's also how the real tools work under the hood: read state, take minimum action.

---

**End of Stage 3 LEARN.** Stage 4 (local LLM via Ollama/Mistral, 4-prompt chain) is the next layer up: takes `raw_posts` + `iocs` + `entities` and produces summaries, intent labels, target profiles, and MITRE technique candidates. The LEARN doc for that lands shortly.
