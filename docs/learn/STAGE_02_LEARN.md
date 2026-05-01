# STAGE 02 — Tor Scraper + Dedup + `raw_posts`

> **Read this on your own time.** Companion doc to the code shipped in Stage 2. Same voice as Stage 1: full depth, jargon unpacked inline, optional **Detour** and **Try this** boxes.

---

## Quick orientation: what does Stage 2 do, in one paragraph?

Stage 1 stood up a fake darknet forum and a Tor hidden service in front of it. Stage 2 is the **scraper**: a Python program that walks across the Tor proxy, hits the forum's JSON API, pulls down every post it hasn't seen yet, and writes them into a local SQLite database (`backend/db/sentinelx.db`). Crucially, it does this **incrementally** — it never re-downloads posts it already has — and it keeps a tiny audit log of what each poll did, so you can answer "what did the scraper do at 3am last night?" by reading a table. That's the whole stage. ~250 lines of Python.

---

## 0. The mental model: what is a "scraper" in CTI, really?

In a real Cyber Threat Intelligence shop, the team that owns the scraper layer is called **collection**. Their job is to talk to the outside world — dark-web forums, paste sites, Telegram channels, leaked-credential dumps, OSINT feeds, vendor APIs. Everything later in the pipeline (NER, LLM analysis, MITRE mapping, the analyst dashboard) eats whatever collection produces.

Every scraper, no matter how big or small, has the same four jobs. Stage 2 does each one:

| Job                    | Why it matters                                                                  | How Stage 2 does it                                                                |
|------------------------|---------------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| **Reach the source**   | The source may be Tor-only, geofenced, behind a login, or rate-limited.         | An `httpx` client routed through Tor's SOCKS5 proxy at `127.0.0.1:9050`.           |
| **Fetch incrementally**| You can't re-download a million-post forum every minute.                        | A *cursor* — "what's the most recent post I already have?" — and `?since=<cursor>` queries. |
| **Dedup**              | Sources expose the same post twice (edits, pagination overlap, retries on flaky network). | A `UNIQUE` constraint on the post id at the database layer. Duplicates get rejected automatically. |
| **Be observable**      | When something breaks at 3am, you need to know *what the last poll actually did*. | A `scraper_runs` audit table — one row per poll, recording the cursor before/after, fetched/inserted/duplicates counts, and the error string if it crashed. |

**If you internalise that table, the rest of this doc is just implementation details.** Every "real" CTI scraper at every CTI vendor has these same four jobs.

> **Detour: why is this called a "cursor"?** "Cursor" is a generic database/streaming word for "a bookmark — the position you've read up to." Kafka consumers track an offset (basically the same idea), git pulls track the last-fetched commit, Apache Airflow DAGs track watermark timestamps. They're all cursors. The shape is universal: *the consumer remembers a position, the source is queryable by it*.

---

## 1. What was built — file map

Five files, all under `backend/`. About 250 lines total.

```
backend/
├── .venv/                  ← Python 3.12 virtualenv (gitignored, host-side)
├── db/
│   ├── __init__.py
│   ├── schema.sql          ← raw_posts + scraper_runs table definitions (DDL)
│   ├── store.py            ← Store class: connection, cursor, inserts, run-log
│   └── sentinelx.db        ← the SQLite file (gitignored, created on first run)
└── scraper/
    ├── __init__.py
    ├── client.py           ← ForumClient: httpx + SOCKS5 + retries
    └── run.py              ← CLI entrypoint (--once / --watch / --reset-cursor)
```

The package is invoked as a module from the repo root:

```bash
backend/.venv/Scripts/python.exe -m backend.scraper.run --once
```

> **Detour: what does `python -m` do, and why not just run the file?**
> `python -m backend.scraper.run` says "find the *package* `backend.scraper.run` and run its `__main__` block." The reason this matters: when you do this, Python puts the **repo root** on `sys.path` (the list of places it looks for imports), not the script's own directory. That's why `from backend.db.store import Store` works inside `run.py` — the import system can find the `backend` package because it's at the repo root. If you `cd backend/scraper/ && python run.py`, that import would fail with `ModuleNotFoundError: No module named 'backend'` because the script's directory (`backend/scraper/`) is on `sys.path`, but the repo root isn't. **Always `python -m` for things organised as packages.**

### 1.1 The verified-working run

```
$ python -m backend.scraper.run --once   # first time
fetched=235 inserted=235 duplicates=0 cursor=0.0 -> 1777120704.17

$ python -m backend.scraper.run --once   # second time, no new posts
fetched=0 inserted=0 duplicates=0
```

That's the whole stage in two log lines: it pulled everything once, then was correctly idle.

> **Try this now:**
> ```bash
> # See what it stored
> sqlite3 backend/db/sentinelx.db "SELECT category, COUNT(*) FROM raw_posts GROUP BY category;"
> sqlite3 backend/db/sentinelx.db "SELECT id, started_at, fetched, inserted, duplicates FROM scraper_runs ORDER BY id DESC LIMIT 3;"
> ```
> The first query shows posts spread across categories. The second shows exactly what each poll did, in order.

---

## 2. File-by-file walkthrough

### 2.1 `backend/db/schema.sql` — the database structure

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

There's a lot to unpack. Let's go column by column.

#### Two timestamps per post: `source_created_at` AND `fetched_at`

This is a real CTI design choice that beginners often miss.

- `source_created_at` is what the *forum* says: "this post was made at epoch X." It comes from the source. The forum's own clock.
- `fetched_at` is what *we* say: "we first saw this post at epoch Y." Our clock.

Real CTI systems always keep both, and it's worth understanding why:
- **Source time** is what analysts care about — "this credential dump appeared on the forum at midnight UTC last night."
- **Fetched time** is what *our own* system observability cares about — "our scraper saw this 4 minutes after the source published. So our SLA for ingestion freshness is 4 minutes." Without `fetched_at` you can't answer questions about your own collection latency.

#### `source_post_id` is the dedup key, not a hash of the body

We're going to dedupe scraped posts somehow. Two reasonable choices:

1. **Use the forum's own post ID** — what we did. The forum hands us a stable integer for each post, so we trust it.
2. **Hash the post body** — generate a SHA256 of the text, use that as the unique key.

Option 2 is what you'd do for *unkeyed* sources: scraping random HTML pages where the same article appears at multiple URLs with different IDs. But it has a nasty failure mode for our case — if a user *edits* their post, the hash changes, and we'd record the edit as a brand-new post. With `source_post_id` an edit just doesn't get re-recorded (which is fine — Stage 3 will re-extract from the same row anyway, and Stage 1's seed data doesn't edit posts).

#### `UNIQUE` on `source_post_id` is what makes dedup actually correct

```sql
source_post_id INTEGER NOT NULL UNIQUE
```

This single keyword is doing more work than it looks. The point: **dedup is enforced by the database, not by the application.** If we tried to insert a post with an ID that's already in the table, SQLite raises an `IntegrityError`. We don't have to maintain a "seen IDs" set in Python, we don't have to remember to check before insert — the DB does it.

> **The pentest framing:** server-side validation is the only validation that matters. Your Burp-Suite life has been spent finding bugs where applications validate input on the *client* and trust it on the *server*. Same principle here, just applied to your own dedup logic. **Trust nothing the application layer says about whether something is a duplicate; let the database be the source of truth.**

#### Indexes on `source_created_at`, `source_thread_id`, `category`

Three indexes, three reasons:

- **`source_created_at`** — speeds up `MAX(source_created_at)` (which we use to read the cursor — see §3.1 below) and any "show me the latest N posts" query.
- **`source_thread_id`** — anticipates Stage 6's API (`/threads/<id>` style queries).
- **`category`** — anticipates filter queries like "show me only marketplace posts."

We pre-create these now because Stage 6 will need them. Adding indexes later is fine but it's a 30-second optimisation we can do today and forget about.

> **Detour: how do indexes actually work?**
> Most SQL databases (SQLite included) build indexes as **B-trees**, which are sorted tree structures that let you find a value in `O(log n)` lookups instead of `O(n)`. The cost: indexes take disk space and slow inserts a little (every insert has to also update the index). The win: queries with `WHERE column = ?` or `ORDER BY column` become massively faster as the table grows. At 235 rows it's irrelevant; at 235,000 it's the difference between snappy and unusable.

#### `scraper_runs` is an audit log, not a state table

The distinction matters. An **audit log** records *what happened*: timestamp, action, outcome. A **state table** stores *what is currently true*. We use `scraper_runs` for the first, not the second. The "current cursor" lives in `MAX(source_created_at)` over `raw_posts` — it's *derived from the data itself*. We'll dive into why in §3.1.

### 2.2 `backend/db/store.py` — the database wrapper class

`Store` is a Python class that wraps a single SQLite connection. There's exactly one writer (the scraper), so we don't need fancy connection pooling, write-ahead logging, or per-thread connections. Three responsibilities:

#### (a) Open the connection + apply the schema

```python
def __init__(self, db_path):
    self.conn = sqlite3.connect(db_path)
    self.conn.row_factory = sqlite3.Row
    self.conn.execute("PRAGMA foreign_keys = ON")
    self._init_schema()

def _init_schema(self):
    with open(SCHEMA_PATH) as f:
        self.conn.executescript(f.read())
```

Things worth knowing:

- **`row_factory = sqlite3.Row`** — already covered in Stage 1. Returns dict-like rows so we can write `row["body"]` instead of `row[5]`.
- **`PRAGMA foreign_keys = ON`** — also covered in Stage 1. SQLite ships with foreign keys disabled. We turn them on as a habit, even though Stage 2 doesn't have any FK columns yet (Stage 3 will).
- **`executescript()` runs multi-statement SQL.** Plain `execute()` only takes ONE statement. Our `schema.sql` has multiple `CREATE` statements, so we need `executescript`. (You'll see a similar split in many DB drivers — execute = one query, executescript / batch / pipeline = multiple.)
- **`CREATE TABLE IF NOT EXISTS`** makes schema-loading **idempotent**: running it twice doesn't error. Means the scraper doesn't have to remember whether it's the first run or the hundredth.

> **Detour: idempotent — what does that actually mean?**
> A function/operation is **idempotent** if running it once gives the same result as running it ten times in a row. `CREATE TABLE IF NOT EXISTS` is idempotent (creates if missing, no-op if present). `INSERT INTO posts (...) VALUES (...)` is *not* idempotent — running it twice creates two rows. Idempotency is a huge deal in distributed systems and ETL: it means you can retry on failure without thinking. Most "production-grade" code is idempotent on purpose.

#### (b) Read the cursor

```python
def get_cursor(self) -> float:
    row = self.conn.execute(
        "SELECT COALESCE(MAX(source_created_at), 0.0) AS c FROM raw_posts"
    ).fetchone()
    return float(row["c"])
```

That four-line method *is* the entire cursor implementation. We'll spend a whole sub-section on why this is better than a separate cursor table (§3.1).

`COALESCE(MAX(...), 0.0)` says: "give me the max value, or 0.0 if there are no rows yet." Without it, the very first run (empty table) would get `MAX(...)` returning NULL and crash when we tried to convert NULL to a float.

#### (c) Insert posts and dedupe

```python
def insert_posts(self, posts):
    inserted = 0
    duplicates = 0
    for p in posts:
        try:
            self.conn.execute("INSERT INTO raw_posts (...) VALUES (...)", (...))
            inserted += 1
        except sqlite3.IntegrityError:
            duplicates += 1
    self.conn.commit()
    return inserted, duplicates
```

The pattern here is: **insert each post, catch the `IntegrityError` if the UNIQUE constraint rejects it, count it as a duplicate.**

Some readers will recognise an alternative — `INSERT OR IGNORE INTO raw_posts ...` — which would skip duplicates silently without raising. Why didn't we use it?

**We want the duplicate count.** `INSERT OR IGNORE` swallows the conflict; you'd have to check `cursor.rowcount` to figure out if anything was actually inserted, and you can't easily get a precise duplicate count. The `try/except` path makes both counts explicit, and at our scale (a few hundred inserts per poll) the cost of a Python exception per duplicate is invisible. **If we were inserting hundreds of thousands of rows per poll**, we'd switch to a bulk `executemany` with `ON CONFLICT DO NOTHING` and read `cur.rowcount` for performance. We're not.

#### (d) The run-log context manager

This is the cleverest piece in `store.py`. Read it slowly:

```python
@contextmanager
def run(self, cursor_before: float):
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

Used like:

```python
with store.run(cursor_before) as h:
    h.fetched = 235
    h.inserted = 235
    h.cursor_after = 1777120704.17
# ← when the `with` block exits, h.finalize() updates the row
```

What's happening:
1. **Enter the `with` block:** insert a `scraper_runs` row immediately with the start time and the cursor *before* the run. This row exists from second one — even if the process dies later, we have a record that the run started.
2. **Yield a `RunHandle`** to the caller. The handle is a tiny mutable object (just `fetched`, `inserted`, `duplicates`, `cursor_after`, `error`). The caller fills it in as it works.
3. **Exit the block on success:** `handle.finalize()` UPDATEs the row with the totals.
4. **Exit on exception:** capture `repr(e)` as the error string, finalize, re-raise.

Either way, **every invocation leaves exactly one row in `scraper_runs`**, and that row truthfully describes what happened.

> **Detour: what's a context manager?**
> Anything you can use with `with X as y:`. Python calls `X.__enter__()` when you enter the block and `X.__exit__()` when you leave it (whether by normal return or by exception). The point: it's a foolproof way to make sure cleanup/finalisation runs. The `@contextmanager` decorator (from `contextlib`) lets you write a context manager as a generator function instead of a class — `yield` is the moment between enter and exit. The same pattern is everywhere: `open(file)`, `lock.acquire()`, `pytest.raises()`, `transaction.atomic()`. If you ever find yourself writing `try: ... finally: cleanup()`, ask if a context manager would be cleaner.

The `_finalized` flag in `RunHandle` is a guard: the exception path explicitly calls `finalize()`, but if the caller had already called it, we don't want to double-update. The flag prevents that.

### 2.3 `backend/scraper/client.py` — the HTTP client

A thin wrapper around `httpx`. Three things worth highlighting.

#### (a) The proxy URL: `socks5://`, NOT `socks5h://`

```python
DEFAULT_SOCKS_PROXY = "socks5://127.0.0.1:9050"
```

This needs context. We covered some of it in Stage 1; here's the full story.

**The problem `.onion` creates:** your local DNS doesn't know how to resolve `.onion` names. Only Tor does. So somewhere in the request flow, the hostname *must* be sent through the proxy and resolved by Tor.

**The historical curl convention:** curl, requests (via PySocks), and most CLI tools use a fake URL scheme `socks5h://` to mean "send the hostname through the proxy" (the `h` = "hostname"). Plain `socks5://` in those tools means "resolve the hostname locally, then connect through the proxy." That second mode would fail for `.onion` because local DNS can't resolve them. So in curl-land, if you're hitting `.onion`, **you must use `socks5h://`** or you'll get DNS errors.

**httpx is different.** httpx 0.27 (which is what we use) **doesn't accept `socks5h://`** — it raises `Unknown scheme for proxy URL`. Why? Because httpx's SOCKS5 transport (built on top of `socksio`) **always** does proxy-side hostname resolution by default. There's no need for the `h` suffix because there's no other mode. So in httpx land:

- Plain `socks5://` = what curl calls `socks5h://` (proxy resolves hostnames). What we want.
- `socks5h://` = error. Doesn't exist as a URL scheme.

Defensible design choice (you almost never *want* local DNS resolution when you're going through Tor — local DNS leaks are a real deanonymisation vector), but it trips up everyone the first time. Took an hour to figure out during the Stage 2 build. **CLAUDE.md gotcha #7 documents this so we don't keep re-fixing it.**

> **The pentest framing:** DNS leaks are a classic OPSEC failure when using Tor. If your browser resolves `evil.onion` through your ISP's DNS before connecting through the proxy, your ISP now knows you wanted to visit that site. Real Tor-aware tooling does proxy-side resolution by default. httpx is doing the right thing, just with a different URL spelling than curl uses.

#### (b) Generous timeouts

```python
DEFAULT_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=60.0)
```

A Tor circuit is three relays plus a hidden-service rendezvous point — five network hops, each potentially on the other side of the world. The default httpx timeout (5 seconds) is fine for a localhost API; it's wildly too short for Tor.

The four numbers correspond to the four phases of a request:
- `connect` — establishing the TCP connection. Slow over Tor.
- `read` — waiting for response bytes after the request is sent. Slow if the server takes a while.
- `write` — sending request bytes. Usually fast unless your upload is huge.
- `pool` — waiting for a connection from the connection pool when many requests are in flight.

30s connect / 60s read are conservative-but-appropriate for Tor. With the defaults you get sporadic `httpx.ReadTimeout` exceptions even when the circuit is fine.

#### (c) `read_onion_hostname()` and `fetch_posts()`

```python
def read_onion_hostname() -> str:
    path = Path("tor_config/hidden_service/hostname")
    addr = path.read_text().strip()
    if not addr.endswith(".onion"):
        raise ValueError(f"unexpected hostname: {addr!r}")
    return addr

def fetch_posts(self, since, limit, category=None):
    r = self.client.get("/api/posts", params={"since": since, "limit": limit, ...})
    r.raise_for_status()
    return r.json()
```

`read_onion_hostname` reads Stage 1's published `.onion` from the bind-mount. The hostname survives `docker compose down` (and even `down -v`) because the bind mount lives on the host. So the scraper can be restarted independently of the forum.

`fetch_posts` is a thin one-liner over the JSON API Stage 1 publishes. The `since=` parameter pushes the cursor filter into the source — the forum returns *only* posts newer than the cursor, so the network and the dedup layer both stay small. (If we'd had to fetch everything every time and dedup on our end, this stage would burn far more bandwidth and CPU.)

### 2.4 `backend/scraper/run.py` — the CLI

Three modes, set up via `argparse`'s `add_mutually_exclusive_group()`:

- **`--once` (default):** one poll, exit. Used in cron jobs / systemd timers, and during development.
- **`--watch --interval 30`:** long-running daemon, polls every 30 seconds. Used when you want continuous ingestion.
- **`--reset-cursor`:** wipes `raw_posts` and `scraper_runs`. Equivalent to "start fresh from epoch."

#### The single-poll function

```python
def poll_once(store, client, batch_limit=1000):
    cursor_before = store.get_cursor()
    with store.run(cursor_before) as h:
        try:
            payload = client.fetch_posts(since=cursor_before, limit=batch_limit)
        except httpx.HTTPError as e:
            h.error = repr(e)
            raise
        posts = payload.get("posts", [])
        h.fetched = len(posts)
        inserted, duplicates = store.insert_posts(posts)
        h.inserted = inserted
        h.duplicates = duplicates
        h.cursor_after = store.get_cursor()
```

Things worth noting:

- **`h.cursor_after = store.get_cursor()`** is re-read from the DB *after* insert, not computed from the payload. Why? Because that's the truth-from-storage that we'll use as `cursor_before` next time. Computing it client-side from `max(p["created_at"] for p in posts)` would be subtly wrong if any rows hit the duplicate path — those wouldn't be in the DB, but their timestamps would still affect the max, and you'd advance past data you don't have.

- **The `with store.run(...) as h:` block** is the run-log context manager from §2.2. It guarantees a `scraper_runs` row regardless of whether the poll succeeds or raises.

#### Watch mode

```python
def run_watch(store, client, interval):
    while True:
        try:
            poll_once(store, client)
        except Exception as e:
            log.warning("poll errored, will retry: %s", e)
        time.sleep(interval)
```

Two important things here:

1. **The loop swallows exceptions.** Tor circuits flap. An `.onion` may be transiently unreachable for 30-90 seconds at a time. We do *not* want one timeout to crash the daemon and require human intervention. The error gets logged AND it's persisted to `scraper_runs.error` by the context manager, so we don't lose observability.

2. **`--once` does NOT swallow exceptions.** That's deliberate. In cron-job land, a non-zero exit code is the right signal for "something's wrong, page someone." Watch mode is the long-running daemon; `--once` is the one-shot.

`KeyboardInterrupt` returns 130 (the conventional Unix exit code for SIGINT — Ctrl-C) so shell scripts can distinguish "user cancelled" from "actual failure."

> **Try this now:**
> ```bash
> # Watch mode for 30 seconds, then kill it
> backend/.venv/Scripts/python.exe -m backend.scraper.run --watch --interval 5 &
> sleep 30
> kill %1
> sqlite3 backend/db/sentinelx.db "SELECT id, fetched, duplicates FROM scraper_runs ORDER BY id DESC LIMIT 6;"
> ```
> You'll see ~6 rows, all with `fetched=0` and `duplicates=0` — no new posts, but each poll is recorded.

---

## 3. Why these choices, vs the alternatives we *didn't* take

### 3.1 Cursor: `MAX(source_created_at)` vs a dedicated state row

This is the single most important design decision in Stage 2. Worth reading carefully.

#### The decision

We do NOT have a `cursor_state` table. `get_cursor()` is just `SELECT MAX(source_created_at) FROM raw_posts`.

#### Why

A separate cursor row introduces a class of bug called **state drift**: two pieces of data that are supposed to track each other can fall out of sync. Concrete failure modes for a `cursor_state` table:

- Inserts succeed but the cursor `UPDATE` fails (or runs in the wrong order, or a crash lands between the two commits): cursor advances past data you don't have, or stays behind data you do.
- Two scraper instances run accidentally: one updates cursor, the other doesn't see the update due to caching, both advance separately.
- Code change adds a new place that inserts rows and forgets to update the cursor.

By definition, **`MAX(source_created_at)` is consistent with what's actually stored, because it's *derived from* what's actually stored.** There is no second source of truth that can disagree.

#### Alternatives we considered

- **Cursor in a JSON file alongside the DB** — same drift problem, plus zero transactional guarantees.
- **Cursor row in a table with transactional update inside `insert_posts`** — works! But adds an extra UPDATE per poll and a foot-gun (forget to update it). The `MAX` approach is simpler and impossible to mess up.
- **Use the highest `source_post_id`** — works for monotonic id sources, but breaks if the source ever assigns ids out of order with respect to created_at. Some forums do this when a moderator edits or restores a post. `created_at` is the semantically correct cursor for "newer than what I've seen."

#### Cost

A `MAX` over an indexed REAL column is `O(log n)` — one B-tree descent. We added the index for exactly this reason. With 235 rows it's free; with 100M rows it's still microseconds.

> **The pentest framing:** state-drift bugs are *exactly* the same family as the ones you exploit in IDOR and race conditions. The application thinks state X is true, the database thinks state Y is true, attacker uses the gap. Our defence: there's no second piece of state. The cursor is computed, not stored.

### 3.2 Dedup: `UNIQUE` + `IntegrityError` vs `INSERT OR IGNORE` vs hash-of-body

Already covered in §2.2 (b). Summary:

- **`UNIQUE` + try/except** (what we did): simple, gives accurate duplicate counts at our scale.
- **`INSERT OR IGNORE`**: performance win at very high insert volume, loses precise duplicate counts.
- **Hash-of-body**: right answer for unkeyed sources (HTML pages), wrong for our keyed source (forum returns stable IDs).

### 3.3 SQLite vs Postgres

For Stage 2, SQLite is correct:

- **Single writer.** The scraper is the only thing writing. SQLite's locking model (one writer at a time) is a non-issue for us.
- **Local file.** No service to run, no credentials to manage. The project bootstraps from `git clone` + `pip install`.
- **Single-machine.** The whole pipeline (Stages 2-6) runs on one laptop. A network DB would be overkill.

We'd revisit Postgres if: multiple writers (e.g. a fan-out of source-specific scrapers running in parallel), or if Stage 7's API moves to a different host. The migration path is short — `sqlite3` and `psycopg` (Postgres driver) share enough of the DB-API 2 standard that the `Store` class is a one-day port.

### 3.4 httpx vs requests vs aiohttp

- **`requests`** has no async story. We don't need async *yet*, but Stage 4 (LLM pipeline) does, and reusing one HTTP library across all stages keeps the codebase clean.
- **`aiohttp`** is async-only. Forces all callers to be async too. Premature complexity for a single-source scraper.
- **`httpx`** does both sync and async with the same API, has a clean Transport abstraction (which is what makes `httpx[socks]` plug in transparently), and is what FastAPI itself uses for its TestClient. Becoming the de-facto modern choice in Python web tooling.

`httpx[socks]` pulls in `socksio`, a pure-Python "sans-IO" SOCKS implementation.

> **Detour: what does "sans-IO" mean?**
> A "sans-IO" library implements the *protocol logic* without doing any actual network I/O itself — it tells you "given these incoming bytes, what should the next outgoing bytes be?" but doesn't open sockets. The benefit: the same protocol code can plug into *both* sync (blocking sockets) and async (non-blocking) network layers. `socksio` is sans-IO; httpx provides the sync and async I/O wrappers around it. Same pattern shows up in `h11` (HTTP/1.1), `h2` (HTTP/2), `wsproto` (WebSocket). A clean architectural idea worth recognising.

### 3.5 Why a context manager for the run log

Two alternatives we didn't pick:

1. **Manual `start_run()` / `finish_run(run_id, ...)` methods.** Forces every caller to wrap in `try/finally`. Easy to forget. Easy to leave runs without a `finished_at` if an exception slips through.

2. **Decorator on `poll_once`.** Also works, but the run-log fields (`fetched`, `inserted`, `cursor_after`) are values *the wrapped function computes*, so the decorator would need a way to get them back. Awkward and ugly.

The context-manager-yielding-a-handle pattern is what `unittest.TestCase.subTest`, `pytest.raises`, and most "scoped resource" APIs in Python use. It guarantees finalisation, gives the caller a clean object to mutate, and reads naturally:

```python
with store.run(cursor_before) as h:
    h.fetched = ...
    h.inserted = ...
```

If you take only one Python idiom away from this stage, take the context-manager-with-handle pattern. It's everywhere in production code.

---

## 4. Tech-stack tour, with industry context

| Component                            | What it is                                                            | Where it shows up in industry                                                                                                                       |
|--------------------------------------|------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Python 3.12**                      | The language.                                                          | Default backend language at most CTI shops (Recorded Future, Mandiant, Flashpoint). Wins: stdlib breadth (`sqlite3` ships in core), the NER/ML ecosystem (Stage 3), FastAPI/Pydantic (Stage 6). |
| **httpx**                            | Modern HTTP client. Sync + async, with pluggable transports.           | Inside FastAPI's TestClient, inside many MLOps stacks (OpenAI's official client wraps it), increasingly in scraper pipelines that have outgrown `requests`. |
| **`httpx[socks]` / socksio**         | SOCKS4/4a/5 transport for httpx. Pure-Python sans-IO implementation.   | Anywhere you talk through Tor, an SSH dynamic forward (`ssh -D`), or a corporate egress proxy. CTI scrapers, OSINT tooling, and red-team infrastructure all rely on SOCKS-aware HTTP clients. |
| **Tor SOCKS5 (port 9050)**           | The local proxy interface to the Tor process.                          | Standard interface for everything that talks to Tor — `torsocks`, the Tor Browser, `requests[socks]`, our scraper. They all speak the same protocol to the same port. |
| **SQLite**                           | Serverless file-based SQL DB. In-process. No server.                   | The most-deployed DB in the world by install count (every Android phone, every iOS app, every browser, every aircraft black box). In CTI specifically: the local cache for ingestion buffers, and the embedded store for analyst tools (e.g. Maltego transforms, MISP's local subset). |
| **`sqlite3` (Python stdlib)**        | DB-API 2 driver for SQLite, ships with CPython.                        | Saves you a dependency. The driver is a thin wrapper around the C library that's already on your machine. |
| **`PRAGMA foreign_keys = ON`**       | Enables FK enforcement (off by default in SQLite for backwards compat). | We don't use FKs in this stage, but turning it on is a cheap-good-habit. Some CTI ETL bugs trace directly to FK enforcement that wasn't on. |
| **`UNIQUE` constraint as dedup gate**| DB-level guarantee.                                                    | This is *the* canonical dedup pattern in ETL. Every "ingest from external source" pipeline you'll meet (Singer/Meltano taps, Airbyte connectors, Segment destinations) does some flavour of "natural key + unique index + INSERT-conflict-handling." |
| **Cursor-based incremental ingest**  | "Give me everything since X."                                          | The standard for log-shipped, append-only sources. Kafka consumers track offsets; CDC tools (Debezium) track LSNs; CTI feeds track `since` timestamps. |
| **Audit-log table per run**          | One row per invocation.                                                | Every prod ETL pipeline has an equivalent. Airflow stores it in `task_instance`; dbt stores it in `run_results.json`; Singer taps stream `STATE` messages. |
| **argparse + `python -m`**           | Stdlib CLI plumbing.                                                   | Simple internal tools rarely need Click/Typer. `python -m package.module` keeps imports working without `sys.path` hacks. |
| **`logging` (stdlib)**               | Hierarchical, level-filtered logging.                                  | The default everywhere. In prod you'd add structured-JSON formatting (`python-json-logger`) and ship to ELK / Loki / Datadog; we'll add that in Stage 6. |

---

## 5. The `socks5h://` story, in one focused dive

This burned an hour during the Stage 2 build, so it gets its own focused section. Worth understanding because the same misconception trips up *everyone* writing their first Tor-aware scraper.

#### Background: what SOCKS5 actually does

SOCKS5 (specified in RFC 1928 if you ever want to read it) is a generic proxy protocol. A client tells the proxy "open a TCP connection to host X on port Y," and the proxy does the TCP work on the client's behalf and pipes bytes back and forth. It's protocol-agnostic — the proxy doesn't care if you're tunnelling HTTP, SSH, IRC, or anything else.

When the client says "open a connection to host X," there are two ways "X" can be resolved to an IP:

- **(a) Local resolution:** the client resolves X to an IP first (using its own DNS), then tells the proxy "connect to IP A.B.C.D port Y."
- **(b) Proxy-side resolution:** the client tells the proxy "connect to *hostname* X port Y," and the proxy does the DNS lookup itself.

For `.onion`, only (b) works. `.onion` is a Tor-internal naming scheme; there's no public DNS authority for it. Your local resolver returns NXDOMAIN. Tor's SOCKS5 server, on the other hand, treats a `.onion` hostname as a request to look up a hidden-service descriptor and rendezvous.

#### The `socks5h://` curl convention

curl, requests (via PySocks), and most CLI tools added a non-standard URL scheme `socks5h://` (`h` = hostname-via-proxy) to **select** mode (b). Plain `socks5://` selects mode (a). So in curl-land, `curl --proxy socks5h://...` is mandatory for `.onion`.

#### httpx's choice

httpx's SOCKS5 transport (which uses `socksio` under the hood) **always uses mode (b)** — proxy-side DNS — and does not implement the `socks5h://` scheme alias.

- Pass `socks5h://` and you get `Unknown scheme for proxy URL`.
- Pass `socks5://` and you get the behaviour curl would call `socks5h://`.

This is a defensible design decision (you almost never *want* mode (a) when going through Tor — local DNS leaks would deanonymise you), but it's surprising if you've internalised the curl convention. Don't fight it; just use `socks5://` with httpx.

> **The takeaway:** different tools spell the same thing differently. The conceptual right thing — "make sure DNS resolution happens in the proxy, not on your local machine" — is what matters. The URL scheme is just syntax.

---

## 6. Where Stage 2 will be revisited later

Things we deliberately did NOT build now, with rough notes for when we will:

- **Multi-source scraping.** Real CTI doesn't scrape one forum; it scrapes dozens. The next time we touch this, `client.py` becomes one of N source modules, and `run.py` orchestrates a registry of them — each with its own cursor (probably keyed by `(source, max_source_created_at)` in a slim source-registry table).
- **Polite ingestion.** No rate limiting, no exponential backoff, no honouring `Retry-After` headers. Fine for a synthetic forum we control. Real sources need token-bucket rate limiters and per-source backoff. `tenacity` is the standard Python library for this.
- **Bulk insert.** When per-poll volume reaches 10k+ rows, swap the per-row `try/except` for `executemany` + `INSERT OR IGNORE` and read `cur.rowcount`. We lose the precise duplicate count but gain ~100× throughput.
- **Schema migrations.** Right now we rely on `CREATE TABLE IF NOT EXISTS` and never alter columns. The moment we want to add a column without wiping data, we'll need a migration tool. `alembic` is the Python standard; for SQLite specifically `yoyo-migrations` is lighter. (Stage 3 actually hits this exact problem with `processed_at` and uses a guarded `ALTER TABLE` workaround. Read on.)
- **Health endpoint / metrics.** `scraper_runs` is internal-facing. A real deployment would expose `/healthz` and Prometheus metrics (`fetched_total`, `dedup_ratio`, `last_success_age_seconds`).
- **Soft-delete + retention.** `raw_posts` grows unbounded. Eventually we'd need a TTL or archive policy. CTI shops typically keep raw data online for 30-180 days and ship older data to cold storage (S3 Glacier-equivalent).

None of those are needed for Stage 3+. The contract Stage 2 publishes — *new rows in `raw_posts` with stable IDs and timestamps* — is exactly what NER/IOC extraction (Stage 3) wants to consume.

---

## 7. The hand-off contract to Stage 3

Stage 3 will run NER + regex IOC extraction over the bodies in `raw_posts`. To make Stage 2's contract explicit:

- **Input table:** `raw_posts`. Stable across Stage 2 reruns.
- **Primary key for joining:** `raw_posts.id` (autoincrement). **Do NOT use `source_post_id` for joins** — it's stable across our DB but is an *external* identifier, and treating it as the primary FK target leaks source semantics into every downstream table. The internal autoincrement `id` is the right join key.
- **Body field:** `body` — plain text, may contain IOCs (IPs, CVEs, hashes, BTC addresses) by construction (Stage 1's seed data).
- **Idempotency contract:** every row in `raw_posts` is immutable once written. Stage 3 may safely cache extraction results keyed by `raw_posts.id`.
- **Cursor for Stage 3:** Stage 3 will track *its own* cursor over `raw_posts` (specifically, a `processed_at` flag column — or in Stage 4's case, a separate `post_processing_state` table). **It does NOT share Stage 2's cursor.** Two stages, two responsibilities, two cursors.

That handoff — *immutable upstream rows + downstream-owned cursors* — is how every real ETL pipeline composes. Worth pausing on: this is the same pattern Kafka uses (immutable log + per-consumer offsets), the same pattern dbt uses (immutable raw layer + materialised models), and the same pattern Airflow uses (immutable XCom + per-task instance state). **You're not learning a quirk of this project; you're learning the shape of all staged data pipelines.**

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

# Pivot: when did we last poll, and was it successful?
sqlite3 backend/db/sentinelx.db "SELECT id, datetime(started_at, 'unixepoch'), fetched, error FROM scraper_runs ORDER BY id DESC LIMIT 1;"
```

---

## 9. The five things to actually remember

1. **Cursor = `MAX(timestamp)` over the data, not a separate state row.** State drift is impossible because there's no second piece of state.

2. **`UNIQUE` constraint at the DB layer is the dedup gate.** The application doesn't have to remember anything; the database does the enforcement. This is the same principle as server-side input validation in web security — trust the layer that *can't* be bypassed.

3. **Two timestamps per record: source-time and fetched-time.** Source-time is what analysts care about; fetched-time is what your *own observability* needs. Real CTI systems always keep both.

4. **`socks5://` in httpx ≠ `socks5://` in curl.** httpx always does proxy-side DNS resolution; the `socks5h://` curl convention is a curl-ism. Don't fight it.

5. **Audit log per run, written from a context manager.** Every poll leaves exactly one row, regardless of success or failure. The `with store.run(...) as h:` pattern guarantees finalisation.

---

**End of Stage 2 LEARN.** Stage 3 (NER + IOC extraction) is the natural follow-on: takes the immutable `raw_posts` rows we just landed and extracts structured facts (IPs, CVEs, hashes, named entities) into dedicated tables.
