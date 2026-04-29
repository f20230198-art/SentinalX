# STAGE 02 — LEARN

> **Stage 2 in one sentence:** a Python scraper that pulls posts from the Stage-1 synthetic `.onion` forum *through Tor*, deduplicates them, and lands them in a local SQLite store with a per-run audit log — incrementally, so each poll only fetches what's new.

This document is the standalone teaching pass for Stage 2. It assumes you've already read `STAGE_01_LEARN.md` (or at least know that Stage 1 produces a v3 hidden service whose hostname is at `tor_config/hidden_service/hostname` and whose forum exposes `GET /api/posts?since=&limit=&category=`).

---

## 0. Mental model — what is a CTI scraper actually doing?

In a real Cyber Threat Intelligence shop, the **collection layer** is the part of the pipeline that talks to the outside world. It pulls observations from sources you don't control: dark-web forums, paste sites, Telegram channels, leak-DBs, OSINT feeds, vendor APIs. Everything downstream — entity extraction, enrichment, MITRE mapping, analyst dashboards — is fed by this layer.

A collection scraper has four jobs, and ours does each one:

| Job                    | Why it matters                                                          | How Stage 2 does it                                                         |
|------------------------|-------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| **Reach the source**   | The source may be Tor-only, geofenced, behind auth, or rate-limited.    | httpx client routed through Tor SOCKS5 at `127.0.0.1:9050`.                 |
| **Fetch incrementally**| You can't re-download the entire forum every minute.                    | Cursor = `MAX(source_created_at)`; ask the source for `since=<cursor>`.     |
| **Dedup**              | Sources expose the same item twice (re-edits, pagination overlap, retries). | `UNIQUE(source_post_id)` constraint + `INSERT … IntegrityError` counting.   |
| **Be observable**      | When something is wrong at 3 AM, you need to know *what the last poll did*. | `scraper_runs` table — one row per invocation, before/after cursors, error. |

If you internalise that table, the rest of this doc is just the implementation details.

---

## 1. What was built

Five files, all under `backend/`. The whole module is ~250 lines.

```
backend/
├── .venv/                  ← Python 3.12 virtualenv (gitignored)
├── db/
│   ├── __init__.py
│   ├── schema.sql          ← raw_posts + scraper_runs DDL
│   ├── store.py            ← Store class: connection, cursor, inserts, run-log
│   └── sentinelx.db        ← created on first run (gitignored)
└── scraper/
    ├── __init__.py
    ├── client.py           ← ForumClient: httpx + SOCKS5
    └── run.py              ← CLI entrypoint (--once / --watch / --reset-cursor)
```

The package is invoked as a module from the repo root:
```bash
backend/.venv/Scripts/python.exe -m backend.scraper.run --once
```
That `python -m` form is what lets `from backend.db.store import Store` work — it puts the repo root on `sys.path`, not the script's directory.

### 1.1 The verified-working run

```
$ python -m backend.scraper.run --once   # first time
fetched=235 inserted=235 duplicates=0 cursor=0.0 -> 1777120704.17

$ python -m backend.scraper.run --once   # second time, no new posts
fetched=0 inserted=0 duplicates=0
```

That's the whole stage in two log lines: it pulled everything once, then was correctly idle.

---

## 2. File-by-file walkthrough

### 2.1 `backend/db/schema.sql`

```sql
CREATE TABLE IF NOT EXISTS raw_posts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_post_id      INTEGER NOT NULL UNIQUE,     -- dedup key
    source_thread_id    INTEGER NOT NULL,
    thread_title        TEXT    NOT NULL,
    category            TEXT    NOT NULL,
    author              TEXT    NOT NULL,
    body                TEXT    NOT NULL,
    source_created_at   REAL    NOT NULL,            -- epoch float, the cursor source
    fetched_at          REAL    NOT NULL             -- when *we* saw it
);

CREATE INDEX IF NOT EXISTS idx_raw_posts_source_created ON raw_posts(source_created_at);
CREATE INDEX IF NOT EXISTS idx_raw_posts_thread         ON raw_posts(source_thread_id);
CREATE INDEX IF NOT EXISTS idx_raw_posts_category       ON raw_posts(category);

CREATE TABLE IF NOT EXISTS scraper_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      REAL    NOT NULL,
    finished_at     REAL,
    cursor_before   REAL    NOT NULL,
    cursor_after    REAL,
    fetched         INTEGER NOT NULL DEFAULT 0,
    inserted        INTEGER NOT NULL DEFAULT 0,
    duplicates      INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);
```

Key design choices:

- **Two timestamps per post.** `source_created_at` is the *forum's* notion of when the post happened (epoch float, exactly what Stage 1's API returns). `fetched_at` is *our* clock, when the row landed in our DB. Real CTI systems always keep both: source time is what analysts care about, fetched time is what your own observability and SLAs are measured against.
- **`source_post_id` is the dedup key, not a hash of the body.** Hashing body content is what you'd do for *unkeyed* sources (e.g. raw HTML pages with no stable id). Our forum hands us a stable integer id, so we use it directly. Hashing would also misclassify edits as new posts.
- **`UNIQUE` constraint on `source_post_id`** is what makes dedup correct under concurrency / retries / partial failures. The DB enforces it; we don't trust application logic to be the only line of defense.
- **Indexes on `source_created_at`, `source_thread_id`, `category`.** The first speeds up `MAX(source_created_at)` (the cursor read) and any "show me the latest N" query. The other two anticipate Stage 6's API filters. We pre-create them now so Stage 6 doesn't have to write a migration.
- **`scraper_runs` is an audit log, not a state table.** It captures *what each invocation did*, not *what the current cursor is*. That second responsibility — current cursor — is owned by `MAX(source_created_at)` over `raw_posts`. See §3.1 for why.

### 2.2 `backend/db/store.py`

The `Store` class wraps a single SQLite connection. There is exactly one writer (the scraper), so we don't need WAL mode, connection pooling, or per-thread connections. Three responsibilities:

**(a) Connection + schema.** `__init__` opens the DB, sets `row_factory = sqlite3.Row` (so rows are dict-like), turns on `PRAGMA foreign_keys = ON`, and `executescript`s `schema.sql`. `executescript` runs multi-statement SQL; `execute` only takes one. The `CREATE … IF NOT EXISTS` guards make this idempotent — running the scraper twice doesn't error.

**(b) Cursor.**
```python
def get_cursor(self) -> float:
    row = self.conn.execute(
        "SELECT COALESCE(MAX(source_created_at), 0.0) AS c FROM raw_posts"
    ).fetchone()
    return float(row["c"])
```
That's the entire cursor implementation. Read §3.1 for why this is *better* than a separate `cursor_state` row.

**(c) Insert + dedup.**
```python
for p in posts:
    try:
        self.conn.execute("INSERT INTO raw_posts (...) VALUES (...)", (...))
        inserted += 1
    except sqlite3.IntegrityError:
        duplicates += 1
self.conn.commit()
return inserted, duplicates
```
Per-row `try/except` rather than `INSERT OR IGNORE`. Why: we *want the count of duplicates*. `INSERT OR IGNORE` swallows the conflict silently and you can only learn the duplicate count by diffing `len(posts)` against `changes()`, which is awkward. The `IntegrityError` path makes the count explicit. Performance cost is negligible at our volume — if we were ingesting millions of rows per poll we'd switch to a bulk `INSERT … ON CONFLICT DO NOTHING` and read `cur.rowcount` for the inserted count. (See §6.)

**(d) Run log via context manager.**
```python
@contextmanager
def run(self, cursor_before: float) -> Iterator["RunHandle"]:
    started = time.time()
    cur = self.conn.execute(
        "INSERT INTO scraper_runs (started_at, cursor_before) VALUES (?, ?)",
        (started, cursor_before),
    )
    run_id = cur.lastrowid
    self.conn.commit()
    handle = RunHandle(self.conn, run_id)
    try:
        yield handle
    except Exception as e:
        handle.error = repr(e)
        handle.finalize()
        raise
    else:
        handle.finalize()
```
Each scraper poll opens a `with store.run(cursor_before) as h:` block. We commit a row *immediately* with the start time and prior cursor, then yield a mutable `RunHandle` the caller fills in (`h.fetched = …`, `h.inserted = …`, `h.cursor_after = …`). On normal exit, `finalize()` UPDATEs the row with the totals; on exception, it stores `repr(e)` in `error` and re-raises. Either way, every invocation leaves *exactly one* row in `scraper_runs`, and that row faithfully reflects what happened. This is the same pattern as a structured logger that flushes on `__exit__`.

`_finalized` is a guard so the exception path's explicit `handle.finalize()` plus the implicit one don't double-update.

### 2.3 `backend/scraper/client.py`

A thin httpx wrapper. Three things worth highlighting:

**Proxy URL.**
```python
DEFAULT_SOCKS_PROXY = "socks5://127.0.0.1:9050"
```
Note the scheme: `socks5://`, not `socks5h://`. Curl/requests use `socks5h://` to mean "resolve hostnames *through* the proxy" (the `h` = "hostname"). This matters for `.onion` because your local DNS resolver cannot resolve `.onion` — only the Tor process can. **httpx 0.27 doesn't accept `socks5h://`** (it raises `Unknown scheme for proxy URL`). Instead, the SOCKS5 transport that ships with `httpx[socks]` (which uses `socksio` underneath) does proxy-side DNS *by default*. So `socks5://` in httpx means what `socks5h://` means in curl. This trips up everyone the first time. CLAUDE.md gotcha #7 documents this so we don't keep re-fixing it.

**Generous timeouts.**
```python
DEFAULT_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=60.0)
```
A Tor circuit is three relays plus a hidden-service rendezvous — five hops total, each potentially on the other side of the planet. 30-second connect / 60-second read are conservative but appropriate. With default 5-second timeouts you get sporadic `httpx.ReadTimeout` even when the circuit is fine.

**`read_onion_hostname()`** reads `tor_config/hidden_service/hostname` (Stage 1's output) and validates it ends with `.onion`. The bind-mount means the address is stable across `docker compose down` — even `down -v`, because the volume that's wiped (`forum_data`) is different from the bind mount (`tor_config/hidden_service`). See Stage 1's gotcha #4.

**`fetch_posts(since, limit, category)`** is a one-liner over the JSON API Stage 1 published. The `since=` parameter pushes the cursor filter into the source — the forum returns *only* posts newer than the cursor, so the network and the dedup layer both stay small.

### 2.4 `backend/scraper/run.py`

The CLI. Three modes via `add_mutually_exclusive_group`:

- `--once` (default): one poll, exit. Used in cron / systemd timer contexts and during development.
- `--watch --interval 30`: long-running loop, polls every N seconds. Used when you want continuous ingestion.
- `--reset-cursor`: wipes `raw_posts` and `scraper_runs`. Equivalent to "start over from epoch."

The single-poll function:
```python
def poll_once(store, client, batch_limit=1000):
    cursor_before = store.get_cursor()
    with store.run(cursor_before) as h:
        try:
            payload = client.fetch_posts(since=cursor_before, limit=batch_limit)
        except httpx.HTTPError as e:
            h.error = repr(e); raise
        posts = payload.get("posts", [])
        h.fetched = len(posts)
        inserted, duplicates = store.insert_posts(posts)
        h.inserted = inserted
        h.duplicates = duplicates
        h.cursor_after = store.get_cursor()
```
Notice that `h.cursor_after` is *re-read from the DB* after insert, not computed from the payload. That's deliberate — it's the truth-from-storage we'll use as `cursor_before` next time. Computing it client-side from `max(p["created_at"] for p in posts)` would be subtly wrong if any of those rows hit the duplicate path.

Watch mode:
```python
def run_watch(store, client, interval):
    while True:
        try:
            poll_once(store, client)
        except Exception as e:
            log.warning("poll errored, will retry: %s", e)
        time.sleep(interval)
```
Crucially, **the loop swallows exceptions**. Tor circuits flap; an `.onion` may be transiently unreachable for 30–90 seconds at a time. We do *not* want a single timeout to crash the daemon and require human intervention. The error is still logged, *and* it's persisted to `scraper_runs.error` by the context manager, so observability is preserved. The `--once` path does *not* swallow — there a non-zero exit code is the right signal for cron/CI.

`KeyboardInterrupt` returns 130 (the conventional Unix exit code for SIGINT) so shell scripts can detect "user cancelled" vs "actual failure."

---

## 3. Why these choices, vs alternatives

### 3.1 Cursor: `MAX(source_created_at)` vs a dedicated state row

**The decision.** No `cursor_state` table. `get_cursor()` is just `SELECT MAX(source_created_at) FROM raw_posts`.

**Why.** A separate cursor row introduces a class of bugs: it can desynchronise from the data. If your inserts succeed but your `UPDATE cursor_state SET value = :new` fails (or runs in the wrong order, or a crash lands between the two commits), the cursor advances past data you don't have, or stays behind data you do — both bad. By definition, `MAX(source_created_at)` is consistent with what's actually stored, because it's *derived from* what's actually stored.

**Alternatives considered:**
- *Store cursor in a JSON file alongside the DB* — same drift problem, plus no transactional guarantee.
- *Cursor table with a transactional update inside `insert_posts`* — works, but requires one extra UPDATE per poll and adds a foot-gun (forgetting to update it). The MAX approach is simpler and impossible to get wrong.
- *Use the highest `source_post_id`* — works for monotonic id sources, but breaks if the source ever assigns ids out of order with respect to `created_at` (some forums do, especially when posts are edited or moderated). `created_at` is the semantically correct cursor for "newer than what I've seen."

**Cost.** A `MAX` over an indexed REAL column is O(log n) — one B-tree descent. We added the index for exactly this reason. With 235 rows it's free; with 100M rows it's still microseconds.

### 3.2 Dedup: `UNIQUE` + `IntegrityError` vs `INSERT OR IGNORE` vs hash dedup

We picked `UNIQUE(source_post_id)` + per-row `try/except`. Already covered above; the deciding factor was wanting an explicit duplicate count. `INSERT OR IGNORE` is what you'd reach for at scale, and we'd swap to it (with a bulk `executemany` and a single `cur.rowcount` read) once we're inserting tens of thousands of rows per poll.

Hash-of-body dedup is the right answer for *un-keyed* sources (HTML pages where the same article is republished at multiple URLs). Wrong answer here, because edits would be misclassified as new content.

### 3.3 SQLite vs Postgres

For Stage 2, SQLite is correct:
- Single writer. SQLite's locking model is a non-issue.
- Local file. No service to run, no creds to manage. Project bootstraps from `git clone` + `pip install`.
- The whole pipeline (Stages 2–6) is single-machine. Network DB is overkill.

We'd revisit if: multiple writers (e.g. a fan-out of source-specific scrapers running in parallel), or if Stage 7's API moves to a different host. The migration path is short — `sqlite3` and `psycopg` share enough of DB-API 2 that the `Store` class is a one-day port.

### 3.4 httpx vs requests vs aiohttp

- **requests** has no async story. We don't need async *yet*, but Stage 4 (LLM pipeline) will, and reusing one HTTP library across stages is nice.
- **aiohttp** is async-only. Forces all callers async. For a single-source scraper that's premature complexity.
- **httpx** does both sync and async with the same API, has a clean Transport abstraction (which is what makes `httpx[socks]` work), and is what FastAPI itself uses for its TestClient. Becoming the de-facto modern choice.

`httpx[socks]` pulls in `socksio`, a pure-Python sans-IO SOCKS implementation. "Sans-IO" means the protocol logic is decoupled from the network layer, which is why the same library plugs into both sync and async httpx.

### 3.5 Why a context manager for the run log

Two alternatives:
1. *Manual `start_run()` / `finish_run(run_id, …)` methods*. Forces every caller to wrap in `try/finally`. Easy to forget. Easy to leave runs without a `finished_at` if an exception slips through.
2. *Decorator on `poll_once`*. Also works, but the run log fields (`fetched`, `inserted`, `cursor_after`) are values *the wrapped function computes*, so the decorator would need a way to receive them back. Awkward.

The context-manager-yielding-a-handle pattern is what `unittest.TestCase.subTest`, `pytest.raises`, and most "scoped resource" APIs in Python use. It guarantees finalisation, gives the caller a clean object to mutate, and reads naturally:
```python
with store.run(cursor_before) as h:
    h.fetched = ...
    h.inserted = ...
```

---

## 4. Tech stack tour, with industry context

| Component             | What it is                                              | Where it shows up in industry                                                                                                                                                          |
|-----------------------|---------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Python 3.12**       | The language.                                            | Default backend language at most CTI shops (Recorded Future, Mandiant, Flashpoint). Python's wins are stdlib breadth (`sqlite3` ships in core), the NER/ML ecosystem (Stage 3), and FastAPI/Pydantic (Stage 6). |
| **httpx**             | Modern HTTP client, sync+async, with pluggable transports.| Used inside FastAPI (TestClient), inside many MLOps stacks (OpenAI's official client wraps it), and increasingly in scraper pipelines that have outgrown `requests`.                  |
| **`httpx[socks]` / socksio** | SOCKS4/4a/5 transport for httpx; sans-IO Python implementation. | Anywhere you need to talk through Tor, an SSH dynamic forward (`ssh -D`), or a corporate egress proxy. CTI scrapers, OSINT pipelines, and red-team tooling all rely on SOCKS-aware HTTP clients. |
| **Tor SOCKS5 (port 9050)** | Local proxy interface to the Tor process.           | Standard interface — every Tor consumer uses it. `torsocks`, the Tor Browser, `requests[socks]`, our scraper, all speak the same protocol to the same port.                            |
| **SQLite**            | File-based relational DB; in-process, no server.         | Default storage for "small data" everywhere — Firefox bookmarks, iOS apps, aircraft black boxes, single-machine analytics. In CTI specifically, used as the local cache for ingestion buffers, and as the embedded store for analyst tools (e.g. Maltego transforms, Misp's local subset). |
| **`sqlite3` (Python stdlib)** | DB-API 2 driver for SQLite, ships in CPython.    | Saves you a dependency. The driver itself is a thin wrapper around the C library that's already on your machine.                                                                       |
| **`PRAGMA foreign_keys = ON`** | Enables FK enforcement (off by default in SQLite for backwards compat). | We don't use FKs in this stage, but turning it on is a cheap-good-habit. Some CTI ETL bugs trace directly to assuming FK enforcement that wasn't on.                                  |
| **`UNIQUE` constraint as dedup gate** | DB-level guarantee.                              | This is *the* canonical dedup pattern in ETL. Every "ingest from external source" pipeline you'll meet (Singer/Meltano taps, Airbyte connectors, Segment destinations) has some flavour of "natural key + unique index + INSERT-conflict-handling." |
| **Cursor-based incremental ingest** | "Give me everything since X."                     | The standard for log-shipped, append-only sources. Kafka consumers track offsets; CDC tools (Debezium) track LSNs; CTI feed pulls track `since` timestamps. The pattern generalises: *the consumer remembers a position, the source is queryable by it.* |
| **Audit-log table per run**  | `scraper_runs` — one row per invocation.          | Every prod ingest pipeline has an equivalent. Airflow stores it in `task_instance`; dbt stores it in `run_results.json`; Singer taps stream `STATE` messages. The reason is universal: when ingestion goes wrong, "what did the last run do?" is the first question on-call asks. |
| **argparse + `python -m`** | Stdlib CLI plumbing.                                | Simple internal tools rarely need Click/Typer. `python -m package.module` is the idiomatic invocation pattern for any package that has both library code and a script entry point — keeps imports working without `sys.path` hacks. |
| **`logging` (stdlib)**     | Hierarchical, level-filtered logging.               | The default everywhere. In prod you'd add structured-JSON formatting (`python-json-logger`) and ship to ELK / Loki / Datadog; we'll add that in Stage 6.                                |

---

## 5. The `socks5h://` story, in detail

This burned an hour during the build, so it gets its own section. Worth knowing because the same misconception trips up every greenfield Tor scraper.

**Background.** SOCKS5 (RFC 1928) lets clients ask the proxy to either:
- (a) **resolve a hostname themselves** and forward the resulting IP, or
- (b) **forward the hostname** and let the proxy resolve it.

For `.onion`, only (b) is possible — `.onion` is a Tor-internal naming scheme; there is no public DNS authority for it. Your local resolver returns NXDOMAIN. Tor's SOCKS5 server, in contrast, treats a `.onion` hostname as a request to look up a hidden-service descriptor and rendezvous.

**The `socks5h://` convention.** curl, requests (via PySocks), and most CLI tools added a non-standard URL scheme `socks5h://` (`h` = hostname-via-proxy) to *select* mode (b). Plain `socks5://` selects mode (a) — local DNS resolution, then proxy the connection. So in curl-land, `curl --proxy socks5h://...` is mandatory for `.onion`.

**httpx's choice.** httpx's SOCKS5 transport (`socksio` under the hood) **always uses mode (b)** — proxy-side DNS — and does not implement the `socks5h://` scheme alias. Pass `socks5h://` and you get `Unknown scheme for proxy URL`. Pass `socks5://` and you get the behaviour curl would call `socks5h://`.

This is a defensible design choice (you almost never *want* mode (a) when you're going through Tor — local DNS leaks are a deanonymisation vector), but it's surprising if you've internalised the curl convention. Don't fight it; just use `socks5://`.

---

## 6. Where Stage 2 will be revisited

Things we deliberately did not build now, with rough notes for when we will:

- **Multi-source.** Real CTI doesn't scrape one forum, it scrapes dozens. The next time we touch this, `client.py` becomes one of N source modules, and `run.py` orchestrates a registry of them — each with its own cursor (probably keyed by `(source, max_source_created_at)` in a slim source-registry table).
- **Polite ingestion.** No rate limiting, no backoff, no `Retry-After` honour. Fine for a synthetic forum we control. Real sources require token-bucket rate limiters and exponential backoff per source — `tenacity` is the standard library for this.
- **Bulk insert.** When per-poll volume reaches 10k+ rows, swap the per-row `try/except` for `executemany` + `INSERT OR IGNORE` and read `cur.rowcount`. We lose the precise duplicate count but gain ~100× throughput.
- **Schema migrations.** Right now we rely on `CREATE TABLE IF NOT EXISTS` and never alter columns. The moment we want to add a column without wiping data, we'll need a migration tool. `alembic` is the Python standard; for SQLite specifically `yoyo-migrations` is lighter.
- **Health endpoint / metrics.** `scraper_runs` is internal-facing. A real deployment would expose `/healthz` and Prometheus metrics (`fetched_total`, `dedup_ratio`, `last_success_age_seconds`).
- **Soft-delete + retention.** `raw_posts` grows unbounded. Eventually we'll need a TTL or archive policy. CTI shops typically keep raw data for 30–180 days online and ship older data to S3-glacier-equivalent.

None of that is needed for Stages 3+. The contract Stage 2 publishes — *new rows in `raw_posts` with stable ids and timestamps* — is exactly what NER/IOC extraction (Stage 3) wants to consume.

---

## 7. Hand-off contract to Stage 3

Stage 3 will run NER + regex IOC extraction over the bodies in `raw_posts`. To make Stage 2's contract explicit:

- **Input table:** `raw_posts`. Stable across Stage 2 reruns.
- **Primary key for joining:** `raw_posts.id` (autoincrement). Do NOT use `source_post_id` for joins — it's stable across our DB but is an *external* identifier; treating it as the primary FK target leaks source semantics into every downstream table.
- **Body field:** `body`, plain text, may contain IOCs (IPs, CVEs, hashes, BTC addresses) by construction (Stage 1's seed data).
- **Idempotency contract:** every row in `raw_posts` is immutable once written. Stage 3 may safely cache extraction results keyed by `raw_posts.id`.
- **Cursor for Stage 3:** Stage 3 will track *its own* cursor over `raw_posts.id` (or a `processed_at` flag column added to `raw_posts`). It does NOT share Stage 2's cursor. Two stages, two responsibilities, two cursors.

That handoff — immutable upstream rows + downstream cursors — is how every real ETL pipeline composes. It's worth pausing on: this is the same pattern Kafka uses (immutable log + per-consumer offsets), the same pattern dbt uses (immutable raw layer + materialised models), and the same pattern Airflow uses (immutable XCom + per-task instance state). You're not learning a quirk of this project; you're learning the shape.

---

## 8. Quick reference

```bash
# One-shot
backend/.venv/Scripts/python.exe -m backend.scraper.run --once

# Continuous (Ctrl-C to stop)
backend/.venv/Scripts/python.exe -m backend.scraper.run --watch --interval 30

# Wipe and re-scrape from epoch
backend/.venv/Scripts/python.exe -m backend.scraper.run --reset-cursor
backend/.venv/Scripts/python.exe -m backend.scraper.run --once

# Inspect
sqlite3 backend/db/sentinelx.db "SELECT category, COUNT(*) FROM raw_posts GROUP BY category;"
sqlite3 backend/db/sentinelx.db "SELECT id, started_at, fetched, inserted, duplicates, error FROM scraper_runs ORDER BY id DESC LIMIT 5;"
```

---

**End of STAGE_02_LEARN.** Stage 2 is now closed: code verified working end-to-end, this LEARN doc shipped. Per CLAUDE.md §5, the remaining checklist item is the git commit, which is deferred to the user's explicit say-so.
