# STAGE 06 — FastAPI Backend

> **Read this on your own time.** Companion to the code shipped in Stage 6. Same voice as Stages 1–5.

---

## Quick orientation: what does Stage 6 do, in one paragraph?

Stages 2–5 turned a stream of forum posts into an enriched relational store: raw posts → IOCs/entities → LLM analyses → MITRE technique mappings. Stage 6 puts a **read-only HTTP API** in front of all of that, so the Stage-7 React frontend (and any other client) can query it without touching SQLite directly. It's a thin FastAPI app — one file, ~250 lines — that joins the five enrichment layers into a few sensible per-post and aggregate views, exposes filters that match how a CTI analyst would actually slice the data ("show me sale posts mapped to T1566", "show me every post that mentions APT29"), and ships JSON. No auth, no writes — the pipeline workers stay the only writers; the API is a query surface.

---

## 0. The mental model: why is the API a separate stage?

Up to now everything has been a script you run: `python -m backend.scraper.run`, `python -m backend.llm.run`. Those are *batch* tools — they process a queue and exit. A frontend can't talk to a script. It needs an always-on process that:

1. **Speaks HTTP** — browsers and curl can both hit it.
2. **Returns JSON** — structured, parseable, no HTML scraping.
3. **Is read-only** — the dashboard reads what the pipeline produced; it does not mutate.
4. **Joins the layers** — the data is split across `raw_posts`, `iocs`, `entities`, `llm_analyses`, `post_techniques`, `mitre_techniques`. The frontend shouldn't need to know that. It should be able to ask "give me everything about post 1" and get all five layers stitched together.

That's the contract Stage 6 implements.

> **Detour: why FastAPI and not Flask?**
> Stage 1's onion forum is Flask, so why a different framework here? Three reasons. (1) FastAPI is async-native — it runs on `uvicorn` over `asyncio`, which means every endpoint is a coroutine and can scale on a single process. Flask is sync-by-default and wants gunicorn workers per CPU to scale. For a dashboard that one user will hit, both work; for industry comparability, async is the modern default. (2) Type-driven validation — FastAPI reads function signatures (`limit: int = Query(50, ge=1, le=500)`) and produces both runtime validation and an OpenAPI schema for free. Flask requires `marshmallow`/`pydantic` glue. (3) Auto-generated docs at `/docs` (Swagger UI) and `/redoc` — useful when you're staring at the API trying to remember what filter to pass. None of these matter at our scale; they all matter at industry scale, and using the modern tool is part of the learning brief.

---

## 1. What was built — file map

```
backend/api/
├── __init__.py        ← empty package marker
└── main.py            ← FastAPI app: routes + helpers, ~250 lines
```

Invocation:

```bash
backend/.venv/Scripts/python.exe -m uvicorn backend.api.main:app --port 8765
```

Then:

- `http://127.0.0.1:8765/docs` → interactive Swagger UI (try every endpoint from the browser).
- `http://127.0.0.1:8765/redoc` → cleaner read-only docs.
- `http://127.0.0.1:8765/openapi.json` → the raw OpenAPI 3.1 spec the frontend will codegen against if we want.

> **Why port 8765 and not 8000?** Splunk Web binds 8000 on the user's machine. The default `--port 8000` returned 303 redirects to `/en-US/...` — that's Splunk, not us. Documented gotcha for the next session.

### 1.1 Verified-working run (2026-04-30)

```
GET /healthz                         → {"status":"ok"}
GET /stats                           → totals + breakdowns (intent, IOC type, top techniques)
GET /posts?limit=2                   → 2 most recent posts, lightweight preview
GET /posts?intent=sale&limit=1       → filtered: 94 total sale posts
GET /posts?technique=T1566&limit=1   → filtered: 153 phishing-mapped posts
GET /posts/1                         → full post + analysis + iocs + entities + techniques
GET /posts/99999                     → 404 (correct error path)
GET /techniques?only_seen=true       → only T-codes attached to >=1 post (24 of 697)
GET /techniques/T1566                → corpus row + the 153 posts that map to it
GET /iocs?ioc_type=ipv4&limit=3      → top IPv4 IOCs by occurrence
GET /entities?label=THREAT_ACTOR     → top threat-actor mentions (APT29 ×5, FIN7 ×4, APT28 ×2)
```

All endpoints return JSON, all 200/404 paths exercised.

---

## 2. The endpoint surface

### 2.1 Meta

| Endpoint | Returns |
|---|---|
| `GET /healthz` | `{"status":"ok"}`. The thing the frontend or a load balancer hits to verify the process is up. |
| `GET /stats` | A single object with totals, category/intent/IOC-type breakdowns, source-of-match split, and top-15 techniques. **One round-trip = the whole dashboard summary panel.** |

### 2.2 Posts

| Endpoint | Purpose |
|---|---|
| `GET /posts` | Paginated list with optional filters: `category`, `intent`, `technique` (T-code), `q` (substring over body+title), `limit` (1–500), `offset`. Returns lightweight rows: id, title, category, author, 280-char body preview, intent, summary. **The list view of the dashboard.** |
| `GET /posts/{id}` | Full single-post detail — joins `raw_posts` + `llm_analyses` + `iocs` + `entities` + `post_techniques`. **The post-detail page.** |

### 2.3 MITRE

| Endpoint | Purpose |
|---|---|
| `GET /techniques` | Browse the 697-technique corpus. Filters: `q` (substring), `only_seen` (boolean — only techniques attached to a post). Each row carries a `post_count` column joined inline. |
| `GET /techniques/{T-code}` | Single technique + the list of posts mapping to it (with score and source). **The reverse lookup: technique → posts.** |

### 2.4 IOCs / entities (cross-post)

| Endpoint | Purpose |
|---|---|
| `GET /iocs` | Aggregated by `(ioc_type, value)` with occurrence count and the post IDs. Lets the frontend build "this IP appears across 10 posts" panels. |
| `GET /entities` | Same shape, aggregated by `(label, text)`. Powers "APT29 appears in posts X, Y, Z." |

> **Why aggregate on the server, not the client?** Returning all 176 IOC rows would force the frontend to do `GROUP BY` in JavaScript. SQLite already has a B-tree index on `(ioc_type, value)` from Stage 3's `UNIQUE` constraint and can do the aggregation in microseconds. Industry rule: do aggregation as close to the data as possible — it minimises bytes-on-the-wire and pushes work to the engine that's optimised for it.

---

## 3. Architecture choices, with rationale

### 3.1 One sqlite3 connection per process, `check_same_thread=False`

```python
def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ...
```

The Python sqlite3 module by default forbids using a connection from any thread other than the one that created it. FastAPI/uvicorn runs sync endpoint functions in a thread pool — different threads on different requests. Three options:

1. **Per-request connection.** Open in the route, close when done. Wastes the connection-setup cost (~0.1ms each) and doesn't scale.
2. **Connection pool.** Real solution at scale — `aiosqlite`, or a pool wrapper. Overkill at our load.
3. **One shared connection with `check_same_thread=False`.** SQLite is fully thread-safe at the C level (in serialized mode, the default). For *read-only* workloads this works perfectly. We're read-only by design.

Picked (3). The pipeline workers (scraper, extraction, LLM, MITRE matcher) hold their own connections in their own processes — there's no contention. SQLite uses a file lock; readers don't block readers.

> **Detour: WAL mode and concurrent writers.** If the API ever needs to write (Stage 8 PDF export logs?), we'd flip the DB to WAL mode (`PRAGMA journal_mode = WAL`). WAL lets writers and readers coexist without blocking each other — readers see a consistent snapshot, writers append to a side log. We don't need it yet because the API doesn't write. Note this for future-you.

### 3.2 Lifespan handler for connection management

```python
@asynccontextmanager
async def lifespan(app):
    app.state.conn = _connect()
    try:
        yield
    finally:
        app.state.conn.close()

app = FastAPI(..., lifespan=lifespan)
```

Old-FastAPI used `@app.on_event("startup")` / `("shutdown")`. Those are deprecated. The modern pattern is a single `lifespan` async context manager — open everything before `yield`, close everything after. This guarantees clean shutdown when the user hits Ctrl-C.

### 3.3 CORS wide-open

```python
app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)
```

Stage 7's frontend will run on `http://localhost:5173` (Vite default), the API on `http://127.0.0.1:8765`. Different origins → browsers block fetches without CORS headers. For a local-only learning project, `allow_origins=["*"]` is fine. **In production this would be restricted to the frontend's actual origin** — a wildcard CORS on a public API is a real security smell because it lets any website on the internet make authenticated requests. We have no auth, so the worst-case is "someone reads our public-by-design data," which doesn't matter.

### 3.4 Server-side pagination, never offset-based at scale

Every list endpoint takes `limit` (1–500, defaults 50–100) and `offset`. Why offset and not cursor pagination?

- **Offset pagination** is `LIMIT N OFFSET M` — easy, but `OFFSET 100000` re-scans the first 100k rows. Bad for big tables.
- **Cursor pagination** uses `WHERE id > last_seen_id ORDER BY id LIMIT N`. O(1) per page regardless of depth.

At 235 posts we don't care. If this were millions, we'd switch. Documenting the choice so it's visible.

### 3.5 Filters: SQL parametrisation, not f-strings

Look at `list_posts()`:

```python
where = []
args = []
if category:
    where.append("rp.category = ?"); args.append(category)
if intent:
    where.append("la.intent = ?"); args.append(intent)
...
where_sql = ("WHERE " + " AND ".join(where)) if where else ""
```

Every user-supplied value goes in as a `?` placeholder, never interpolated. SQL injection is the canonical OWASP top-10 risk; on a SQLite file the worst payload could `DROP TABLE raw_posts`. We dodge it by never trusting user input as code.

The column names *are* string-interpolated, but they're constants in our code — never user-supplied — so that's safe.

### 3.6 The big join in `/posts/{id}`

```python
SELECT pt.technique_id, pt.source, pt.score, pt.evidence,
       mt.name, mt.tactics, mt.url, mt.is_subtechnique, mt.parent_id
FROM post_techniques pt LEFT JOIN mitre_techniques mt
  ON mt.technique_id = pt.technique_id
WHERE pt.raw_post_id = ?
ORDER BY CASE pt.source WHEN 'llm_verified' THEN 0 WHEN 'semantic' THEN 1 ELSE 2 END,
         pt.score DESC NULLS LAST, pt.technique_id
```

Two things worth noticing:

1. **`LEFT JOIN`** because `pt.technique_id` for `llm_unverified` rows might not exist in `mitre_techniques` (that's literally what unverified means). LEFT JOIN keeps the row, name comes back NULL.
2. **`CASE … ORDER BY`** so the response sorts `llm_verified` first (most trustworthy), then `semantic` (corpus-grounded discoveries), then `llm_unverified` (hallucinations) last. The frontend gets a sensible ranking for free.

---

## 4. Data shapes (worked example)

### 4.1 `GET /posts/1` (excerpt)

```json
{
  "post": { "id": 1, "thread_title": "[FRESH] 25M Okta combos...", "body": "..." },
  "analysis": {
    "summary": "Actor is offering a dataset of 25 million Okta credentials...",
    "intent": "sale",
    "targets": {"industries": [], "geographies": ["US"], "victim_types": ["individuals"]},
    "techniques": {"techniques": [{"id":"T1566","name":"Phishing","evidence":"..."}, ...]}
  },
  "iocs": [{"ioc_type":"btc","value":"bc1qar0srrr7xfk...","span_start":186, ...}],
  "entities": [{"label":"ORG","text":"Okta", ...}],
  "techniques": [
    {"technique_id":"T1566","source":"llm_verified","score":null,
     "evidence":"...","name":"Phishing","tactics":["initial-access"],"url":"https://attack.mitre.org/techniques/T1566"},
    ...
  ]
}
```

Note `analysis.targets` and `analysis.techniques` are **deserialised JSON**, not strings — the storage format (`targets_json` TEXT) is a backend detail that the API hides. `_maybe_json` in `main.py` parses on the way out.

### 4.2 `GET /stats`

This is what the dashboard summary panel will be built on. From the live DB right now:

```
posts_total                235
posts_extracted            235
posts_llm_analysed         235
posts_mitre_matched        235
iocs_total                 176
entities_total             239
techniques_corpus          697
post_techniques_total      292
by_intent                  discussion:113, sale:94, other:20, recruitment:8
by_technique_source        llm_verified:232, llm_unverified:45, semantic:15
top_techniques             T1566:153, T1078:34, T1086:14, ...
```

One round-trip → one dashboard panel.

---

## 5. Tech stack & why each piece

| Piece | Role | Why this and not something else |
|---|---|---|
| `fastapi` 0.115 | The HTTP framework | Modern, async, type-driven validation, free OpenAPI docs. Industry default for new Python APIs. |
| `uvicorn[standard]` | ASGI server | The reference ASGI server for FastAPI/Starlette. `[standard]` pulls fast-path deps (`httptools`, `websockets`, `watchfiles` for `--reload`). |
| `pydantic` (transitive) | Request/response validation | FastAPI reads function-signature types (`limit: int = Query(50, ge=1, le=500)`) and turns them into pydantic validators automatically. We didn't have to write any models. |
| Raw `sqlite3` (stdlib) | DB access | Same connection the pipeline uses. Read-only here. No ORM — for queries this shape, raw SQL is *more* readable than SQLAlchemy. |
| `CORSMiddleware` | Cross-origin permission | Required so the Vite dev server (port 5173) can fetch from the API (port 8765). |

What we deliberately did **not** add:

- **An ORM (SQLAlchemy / SQLModel).** Our queries are read-only and join-heavy; ORMs shine for write-heavy CRUD with complex object graphs. Raw SQL with `sqlite3.Row` is the right level of abstraction here.
- **Pydantic response models.** FastAPI lets you declare `response_model=PostDetail` and it validates the return shape. Useful in big teams; here it would be ~150 lines of model declarations to do what the database already does. Skipped intentionally.
- **Auth (JWT, OAuth, API keys).** No auth needed for a local-only learning project. In production this would be the first thing to add.
- **Caching layer (Redis / in-memory LRU).** SQLite + indexed reads is already sub-millisecond on this dataset. Caching would add a coherence problem (when the pipeline writes, the cache is stale) without speeding anything up.
- **Background tasks / webhooks.** The API doesn't write or run jobs. The pipeline workers do that.

---

## 6. How this is used in industry

What we built is a **read API over an enrichment store**. That's the core shape of every modern CTI / SIEM / observability product:

- **Splunk's REST API**, **Elastic's search API**, **Microsoft Sentinel's Logs API**, **CrowdStrike's Falcon API**, **Recorded Future's Connect API** — all expose paginated, filtered, JSON read endpoints over an ingest+enrichment pipeline. The shapes differ; the shape of the *contract* is identical to what we've done.
- **OpenAPI / Swagger** is the lingua franca for API documentation and codegen. Our `/openapi.json` could feed `openapi-typescript` and produce a fully-typed TypeScript client for the frontend in one command. That's how every modern frontend-backend stack works in 2026.
- The **read-only-API + separate-write-pipeline** split is called CQRS-lite (Command-Query Responsibility Segregation). Big benefit: the API can scale horizontally (no writes = no coordination) while the pipeline scales independently. Production shops formalise this with separate deployments; here we get it for free because we just didn't write any write endpoints.
- **Aggregations on the server** (our `/stats`, `/iocs`, `/entities` aggregated views) is exactly how dashboards like Grafana, Kibana, and Splunk's frontend work — they ask the storage tier to do the GROUP BY because the storage tier has indexes and the frontend doesn't.
- **Server-side filtering with parametrised SQL** is how every public-facing API in the world avoids SQL injection. The pattern transfers verbatim.

If a CTI startup hired you tomorrow, the first ticket would plausibly be "add an endpoint to filter by [new dimension]" — copy-paste one of our `WHERE` blocks, add a `Query` parameter, done. The skill that scales is *thinking in joins and indexes*, which is what Stage 6 forces you to do.

---

## 7. What Stage 6 does **not** do (deferred)

- **Auth.** Stage 7 might add a single hard-coded API key once the demo cares about not exposing the live `.onion` corpus to the broader internet.
- **WebSocket / SSE for live updates.** Stage 7's `--watch`-driven demo could push new posts into the dashboard via WebSocket. Currently the frontend will just poll `/stats` and `/posts` on a timer.
- **PDF export.** Stage 8.
- **Attack-graph endpoint.** Stage 8 will likely add `/graph` returning nodes (techniques, posts, IOCs, actors) and edges suitable for vis.js to render.
- **Search across IOC values / entity texts as a single endpoint.** Currently the frontend has to hit `/iocs` and `/entities` separately. A unified `/search?q=…` could come in Stage 7 if the UX needs it.

---

## 8. Gotchas observed during this stage

1. **Splunk on port 8000.** The user has Splunk Web bound to localhost:8000. Default `uvicorn --port 8000` silently fails to bind and curl gets 303 redirects to `/en-US/...`. Use `--port 8765` (or any other free port). Documented in CLAUDE.md gotchas.
2. **`check_same_thread=False`.** Required because uvicorn runs sync route handlers in a threadpool. Safe for read-only workload; would need re-thinking if we add writes.
3. **`processed_at` column in `raw_posts`.** Was added by `Store._init_schema` via guarded ALTER for old DBs. The API just `SELECT *`s and trusts the column exists; if a fresh DB is created with the current schema.sql, the column will be created via the same code path.
4. **`GROUP_CONCAT(raw_post_id)`** returns a comma-joined TEXT. The API splits and casts to `int` before returning so the frontend gets a real array. Don't change the API to return the raw string — it's a footgun for the consumer.
5. **`only_seen` correlated subquery in `/techniques`.** `EXISTS (SELECT 1 FROM post_techniques pt WHERE pt.technique_id = mt.technique_id)` is fast because `idx_pt_tech` indexes `post_techniques.technique_id`. Without that index it'd be a full scan per row.

---

## 9. End-of-stage status

- ✅ FastAPI app shipped in `backend/api/main.py`.
- ✅ Endpoints: `/healthz`, `/stats`, `/posts`, `/posts/{id}`, `/techniques`, `/techniques/{T-code}`, `/iocs`, `/entities`. All exercised against the live DB and return correct data.
- ✅ Read-only by design; pipeline stays the sole writer.
- ✅ CORS open for local Vite dev server.
- ✅ Auto-generated OpenAPI docs at `/docs` and `/redoc`.
- ⬜ Stage 7 (React + Vite + Tailwind frontend) will consume this API and render the dashboard.

The pipeline is now a queryable system, not just a database. Anything Stage 7 needs to build a dashboard is one HTTP call away.
