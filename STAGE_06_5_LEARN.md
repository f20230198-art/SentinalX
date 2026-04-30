# Stage 6.5 — Investigations, Lenses, and Diagnostics

> Mini-stage between the read-only API (Stage 6) and the React frontend (Stage 7). Adds three things inspired by reviewing Apurv Singh Gautam's [Robin](https://github.com/apurvsinghgautam/robin) (the AI dark-web OSINT tool NetworkChuck reviewed): named saved views called *Investigations*, four cross-post analytical *Lenses*, and a deeper `/healthz/full` diagnostic endpoint. None of these change the pipeline; they all live in the API layer.

---

## 1. Why this stage exists

Stage 6 gave us a clean read-only API over the corpus. Stage 7 needs *something to render in the sidebar* besides "list of posts" — a learning-grade dashboard with no saved state feels thin. Two patterns from Robin mapped cleanly:

1. **Investigations** — Robin lets a user save a query → search → scrape → summary as a named, replayable artifact. Our equivalent is a *named saved view*: a filter over the corpus, optionally tied to a *lens*, optionally with a fused cross-post summary. Same UX shape; different underlying data model (we already have the corpus, we don't re-search at rerun time).
2. **Lenses** — Robin has 4 preset prompts (`threat_intel`, `ransomware`, `personal_identity`, `corporate_espionage`). A lens is just "the same posts re-summarised through a different analyst's perspective." We borrowed the 4 categories but rewrote the prompts so they reference our schema (IOCs, entities, MITRE techniques) instead of asking the LLM to extract structure from raw text again.

The third addition, `/healthz/full`, came from Robin's health-check page. Useful both as a demo prop ("here's the system status") and as actual diagnostics when something breaks during a viva.

**What we deliberately did NOT borrow from Robin:** its 16-search-engine discovery flow. That's exactly what makes Robin Robin — it's a *search* tool. SentinelX is a *monitoring* tool. Different identity, different problem. Bolting search on would dilute the project.

### The identity sentence (use this in the viva)

- **Robin = ad-hoc OSINT investigation.** Stateless, query-driven. "I have a question, find sources, summarise them, done."
- **SentinelX = continuous threat-intelligence pipeline.** Stateful, target-driven, longitudinal. "Monitor this target over time, extract structured intel, map it to MITRE, surface relationships."

If anyone asks "isn't this just Robin?" — *no, Robin is a search engine, SentinelX is an observatory.*

---

## 2. The Investigation data model

```sql
CREATE TABLE investigations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    description     TEXT,
    filters_json    TEXT    NOT NULL,
    lens            TEXT,
    summary         TEXT,
    summary_model   TEXT,
    summary_post_ids TEXT,
    created_at      REAL    NOT NULL,
    updated_at      REAL    NOT NULL,
    last_run_at     REAL
);
```

Three design decisions worth pulling out:

### 2.1 Live views, not snapshots

`filters_json` is stored verbatim and **re-evaluated on every read**. An investigation created last week against `{intent: "sale", q: "credential"}` will pick up newly-ingested credential-sale posts on the next view or rerun. This is the right behaviour for a continuous-monitoring tool — the alternative (freeze the post-id list at creation) would mean investigations rot the moment new data arrives.

The trade-off: `summary_post_ids` is *that rerun's* input set, not "the investigation's posts." Two reruns a week apart can summarise different sets, and that's expected.

### 2.2 No summary history

We store one `summary` per investigation; reruns overwrite. We do not have an `investigation_summaries(id, investigation_id, summary, ran_at)` history table.

Reasoning: storage cost > evidence value for a learning project. If you need to compare summaries across time, that's a separate stage. The trace in `llm_runs` already tells you *that* a rerun happened, and `last_run_at` tells you *when*. The actual prior text is not load-bearing for any feature we have.

This is the kind of YAGNI decision that's worth being explicit about — it's exactly the sort of "obvious future feature" that a reviewer might call out, and "we considered it and decided no, here's why" is a stronger answer than "oh, good idea, let me add it."

### 2.3 Lens names live in code, not the DB

`lens` is a string column, but the valid values come from `backend/llm/lenses.py:LENSES`. We do NOT have a `lenses(name, system_prompt)` table.

Reasoning: prompts are code, not data. Versioning prompts via DB rows means you can't `git diff` them, can't test them in CI, and can't comment on them. Putting them in a Python module means evolving the prompt is a one-line PR, not a migration + a row update + a deployment.

The DB still validates that the lens exists (via `get_lens(name)` raising `KeyError` if invalid), so we get integrity without the table.

---

## 3. The filter→SQL translation

`backend/api/investigations.py:_build_where()` translates the JSON filter dict into a SQL `WHERE` + `JOIN` pair:

```python
filter dict                           generated SQL fragment
─────────────────────────────────────  ────────────────────────────────────────
{"category": "marketplace"}           WHERE rp.category = ?
{"intent": "sale"}                    LEFT JOIN llm_analyses la ...; la.intent = ?
{"technique": "T1566"}                JOIN post_techniques pt_f ON ... AND pt_f.technique_id = ?
{"q": "credential"}                   WHERE (rp.body LIKE ? OR rp.thread_title LIKE ?)
{"ioc_type": "btc"}                   JOIN iocs ioc_f ON ... AND ioc_f.ioc_type = ?
{"since": 1745000000.0}               WHERE rp.source_created_at >= ?
{"until": 1746000000.0}               WHERE rp.source_created_at <= ?
{"post_ids": [1, 2, 3]}               WHERE rp.id IN (?, ?, ?)
```

All combine with `AND`. All use parametrised `?` placeholders — no f-string interpolation of user input ever. The function returns `(where_sql, args, join_sql)` and the caller stitches them into the final query.

### 3.1 The bug we caught during the build

Original draft used `args.insert(0, …)` to push JOIN-side bindings to the front of a single args list. When BOTH `technique` and `ioc_type` filters were set, the two `insert(0, …)` calls happened in opposite order from the JOIN clauses, silently swapping the bindings — `technique="T1566"` ended up filtering on `ioc_type` and vice versa. SQLite happily ran the broken query and returned zero rows.

**Fix:** maintain `join_args` and `where_args` as separate lists. JOIN clauses are appended to `joins` in evaluation order, and their args are appended to `join_args` in the same order. At the end we return `join_args + where_args` so the positional `?` placeholders stay aligned regardless of which filter keys are present.

Lesson: when you need positional ordering across multiple sources of args, keep the sources separate until you compose them. `list.insert(0, …)` is a code smell whenever there's more than one caller — it imposes a global ordering that's invisible at the call site.

---

## 4. Lenses: prompts that reference our schema

Robin's lenses are all "given this raw text, please extract X, Y, Z." That makes sense for Robin because Robin only has scraped HTML.

We already extracted X, Y, Z in Stages 3–5. So our lens prompts feed those structured fields *as authoritative context* and ask the LLM to write a fused report on top of them. The post block format is:

```
=== POST [#42] === (marketplace / dredsec)
TITLE: WTS Cobalt Strike beacon configs
BODY: <body, clipped to 1200 chars>
INTENT: sale
PRIOR SUMMARY: <Stage-4 per-post summary>
IOCS: ipv4=10.0.0.5; cve=CVE-2024-1086; btc=bc1q…
ENTITIES: ORG=Cobalt Strike; THREAT_ACTOR=APT29
MITRE: T1059.003; T1566 Phishing; T1078 Valid Accounts
```

The lens system prompt explicitly tells the model to "use the structured fields as authoritative; treat the body as supporting context." That changes what the LLM does. Instead of being a flaky NER + IOC extractor (we already did that more reliably with spaCy + regex), it becomes a **synthesis engine** — clustering across posts, attributing IOCs to source posts via `[#id]` citations, identifying the most active operation, recommending hunt actions.

The `[#id]` citation convention is small but matters:

- It anchors the LLM to specific evidence rather than vague generalities.
- The frontend can render `[#210]` as a clickable link to the post detail page.
- It gives the user (and a viva examiner) a way to spot-check claims.

You can see this working in the verified rerun output:

> `okta-sso.help [#210, #162, #148, #123, #100, #30]`
>
> `ipv4=192.42.116.16 [#210]`
>
> `Logistics company [#210]`

Every claim is back-traceable to a post id in the rerun's input set.

### 4.1 The four lenses

| Name | When to use | Output sections |
|---|---|---|
| `threat_intel` | Default. General-purpose CTI report. | Exec summary; actors; capabilities/TTPs; targets; pivot-worthy IOCs; next steps. |
| `ransomware` | Ransomware-flavoured posts. | Ops identified; malware/tooling; initial access + CVEs; C2/staging; victims; MITRE chain; detection. |
| `personal_identity` | Credential dumps, breach data. | Exposure categories; breach sources; volume/recency; named individuals (only if already public); severity; protective recs. |
| `corporate_espionage` | Targeted org campaigns. | Affected orgs; type of compromise; actors/motivations; business impact; intrusion path; IR recs. |

The `personal_identity` lens has an explicit "do NOT invent or extrapolate identities" guardrail in the system prompt. That's not paranoia — it's the same guardrail Robin has, and for the same reason: a hallucinated dox is worse than no dox.

### 4.2 Caps that prevent quality cliffs

```python
MAX_POSTS_PER_RERUN = 20   # cap rerun input size
MAX_BODY_CHARS      = 1200 # per-post body clip
```

Mistral 7B's effective context window is generous on paper but quality starts dropping past ~8k tokens of meaningful input. At ~400 tokens of body+enrichment per packed post we hit that ceiling around 20 posts. Bigger investigations get sampled (newest first, since we order by `source_created_at DESC`).

If we move to a longer-context model (Claude Sonnet 4.6 has 200k tokens, GPT-5.1 has 1M) we raise these caps. Until then, the cap is there to protect output quality, not for any storage or cost reason.

---

## 5. The `/healthz/full` diagnostic endpoint

`/healthz` (Stage 6) is the standard "is the process up?" smoke test — used by load balancers and uptime monitors. `/healthz/full` is the dashboard's status card, with four independent checks:

```python
{
  "status": "ok" | "degraded",
  "checks": {
    "db":         { "status": "up", "latency_ms": 0, "path": "..." },
    "tor_socks":  { "status": "up", "latency_ms": 12 },
    "ollama":     { "status": "up", "latency_ms": 696, "models": ["mistral:latest"] },
    "pipeline":   { "status": "up", "pending_extraction": 0, "pending_llm": 0, "pending_mitre": 0 }
  }
}
```

Two design notes:

### 5.1 Each check is independent

If Tor is down because Docker is paused, that does not mark `db` or `ollama` as degraded. Each probe runs in its own try/except and writes its own sub-status. The top-level `status` flips to `"degraded"` only if a *required* dependency (DB) fails — Tor down is normal during local development.

Industry equivalent: Kubernetes `livenessProbe` (process up?) vs `readinessProbe` (can serve traffic?) vs Prometheus exporters (metrics, even when degraded). `/healthz` is liveness; `/healthz/full` is closer to a metrics endpoint shaped for human consumption.

### 5.2 The Tor probe is TCP-only

We do `socket.create_connection(("127.0.0.1", 9050), timeout=2.0)` and that's it. We do NOT actually open a circuit and fetch a `.onion` page. Two reasons:

1. **Latency.** A real .onion fetch takes 5–15s. A health endpoint that takes 15s is not a health endpoint.
2. **Port pool hygiene.** Tor's SOCKS5 port has a finite circuit pool. The scraper holds long-lived circuits to the forum's hidden service. If the API process keeps opening throwaway circuits to test Tor, it dirties the pool and introduces flakiness in the scraper. TCP-probe-only avoids this entirely.

### 5.3 Lazy import of OllamaClient

```python
# inside the handler
from backend.llm.client import OllamaClient
```

Imported inside the function body, not at module top. Reasoning: 99% of requests hit `/healthz` (cheap) or `/posts` (no LLM needed). Only `/healthz/full` and `POST /investigations/{id}/rerun` need the Ollama HTTP client. Top-level import would pay httpx + dataclass setup costs on every cold start of the API process for no benefit. Lazy import means cold starts stay fast and the dependency only gets resolved when actually needed.

This is the same pattern Django uses for `apps.ready()` and FastAPI uses internally for some optional integrations — defer expensive setup until first use.

---

## 6. The endpoint surface (Stage 6.5 additions)

```
GET    /lenses                                  → 4 lens metadata items
GET    /investigations                          → list, newest first
POST   /investigations                          → create (name + filters + optional lens)
GET    /investigations/{id}                     → detail + matched_total + matched_posts[:200]
PATCH  /investigations/{id}                     → partial update (name/desc/filters/lens)
DELETE /investigations/{id}                     → 204 on success, 404 on miss
POST   /investigations/{id}/rerun               → fires lens prompt on Mistral, persists summary
GET    /healthz/full                            → 4-check diagnostic
```

The `rerun` endpoint is the only one that can take 30–90 seconds. Everything else returns in <100ms. That's important for the frontend — `rerun` needs a loading state with a "this will take a minute" hint; the others can be fire-and-forget.

---

## 7. Verified end-to-end (2026-04-30)

Walking through the actual verification because this is what gives the stage credibility:

1. **Schema migration** — `executescript` over `backend/db/schema.sql` against the live DB. Confirmed via `PRAGMA table_info(investigations)` showing all 11 columns.
2. **`GET /lenses`** — returned 4 items with name/label/description.
3. **Create investigation** — `POST` with `{name, filters: {intent: "sale", q: "credential"}, lens: "ransomware"}`. Got back `id=1` with all fields.
4. **Filter eval** — `GET /investigations/1` returned `matched_total=6` and the 6 VPN-cred-sale posts (210, 162, 148, 123, 100, 30).
5. **`/healthz/full`** — db up (0ms), ollama up (696ms, `mistral:latest` listed), pipeline up (0/0/0 backlog). Tor down (Docker stack paused — expected).
6. **`POST /investigations/1/rerun`** — 36.78 seconds wall-clock. Mistral returned a 1744-char ransomware-lens report. Spot-checked: every IOC in the report (`okta-sso.help`, the per-post IPv4s) traces back to the actual `iocs` table for those 6 post ids; every MITRE technique (T1078, T1087, T1566) is actually present in `post_techniques` for those posts; every named org (logistics, EU manufacturing, Indian fintech, municipal government) appears in either thread titles or extracted entities. The model did not hallucinate sources.
7. **Persistence** — `last_run_at`, `summary_model='mistral'`, `summary_post_ids=[210,162,148,123,100,30]`, `summary` (1744 chars) all readable on subsequent GET.
8. **Negative paths** — DELETE returned 204; subsequent GET returned 404; POST with invalid lens returned 400.

This is genuinely working, not "it compiled and the smoke test passed."

---

## 8. Industry parallel: where this pattern lives in real CTI tools

The Investigation + Lens pattern is not novel to Robin. It's the standard shape for analyst-facing CTI tooling:

| Tool | Equivalent of "investigation" | Equivalent of "lens" |
|---|---|---|
| Splunk Enterprise Security | *Notable event* / *Investigation timeline* | Saved-search analytic stories (Splunk ESCU) |
| Microsoft Sentinel | *Incident* (with linked alerts/entities) | *Workbooks* — same data, different visualisation/narrative |
| CrowdStrike Falcon | *Detection* / *Hunt* | *Hunting query templates* by adversary or technique |
| Recorded Future | *Watchlist* / *Custom view* | *Intelligence Card* templates per entity type |
| MISP | *Event* with tags | Galaxy clusters (threat-actor / malware / sector lenses) |

The two-axis design — "what posts/events am I looking at" × "what narrative am I asking for" — is universal. It separates *scope* (filter) from *interpretation* (lens), which is exactly what an analyst's mental model looks like. We've built a small but honest version of the same pattern.

---

## 9. What this unlocks for Stage 7

The frontend can now render:

- **Saved-investigations sidebar** — list, click to load, "Run lens" button per investigation.
- **Lens selector** on the investigation detail view — pick from 4, hit run, watch the summary stream in (we'd need to add SSE for streaming; for v1 the 36s spinner is fine).
- **Citation links** — render `[#210]` in summaries as a hyperlink to `/post/210`. This is the kind of small touch that makes the dashboard feel real.
- **Health card** on the dashboard top — a 4-tile status panel fed by `/healthz/full`. Doubles as a debugging aid when something breaks during the demo.
- **"Why this technique?" affordance** — when a user clicks a MITRE technique in the timeline, we can spin up a one-shot investigation behind the scenes (`{technique: T1566}`, lens `threat_intel`, run, show summary). No new endpoints needed.

None of these require additional backend work. The Stage 6.5 surface covers them all.

---

## 10. Files touched

```
backend/db/schema.sql              + investigations table + index
backend/llm/lenses.py              new — 4 Lens dataclass entries + system prompts
backend/api/investigations.py      new — filter→SQL, evaluate, rerun, CRUD helpers
backend/api/main.py                + 8 endpoints, version bump 0.6.0 → 0.6.5
```

No changes to the pipeline (Stages 2–5). No new external dependencies. Migration is a single `CREATE TABLE IF NOT EXISTS` + index — re-running it on an existing DB is a no-op.

---

## 11. Loose ends / what's NOT done

1. **No SSE for rerun streaming.** Today the rerun call is synchronous and the client waits 36s. For Stage 7's UX we'll likely add `POST /investigations/{id}/rerun?stream=true` that returns a text/event-stream of Mistral tokens. Easy add when needed.
2. **No history of past summaries.** As discussed in §2.2, deliberate.
3. **No background queue.** A rerun blocks the API's threadpool worker for ~30s. With one user that's fine; if we ever had concurrent reruns it would matter. The fix when it matters is `arq` or `rq` — out of scope today.
4. **No auth.** Same as Stage 6 — no auth on any endpoint. Local-only project. If this ever ran public we'd add API keys + rate limit per key.
5. **Lens prompts are not yet user-customisable.** Robin lets you append "custom instructions" to a preset. Adding `custom_system_suffix` to the `investigations` row is a 5-line change if/when needed.

---

## 12. End-of-stage status

- Schema migration applied and verified against live DB.
- 8 new endpoints registered, 7 of them exercised via curl, 1 (the rerun) exercised against real Mistral with verified non-hallucinated output.
- `STAGE_06_5_LEARN.md` (this doc) shipped.
- `CLAUDE.md` §3 updated; stage table now includes the 6.5 row.
- `PROGRESS.md` has a new dated entry.
- No git commit yet — deferred per CLAUDE.md §8.

Stage 6.5 done. Stage 7 unblocked.
