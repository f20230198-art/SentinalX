# SentinelX I — Technical Project Report

**Author:** Srivathsa H Honyal
**Project window:** April–May 2026
**Document version:** 1.0 (2026-05-01)

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Problem statement and motivation](#2-problem-statement-and-motivation)
3. [Methodology](#3-methodology)
4. [System architecture](#4-system-architecture)
5. [Component-by-component implementation](#5-component-by-component-implementation)
6. [Data model](#6-data-model)
7. [Design decisions and trade-offs](#7-design-decisions-and-trade-offs)
8. [End-to-end demo script](#8-end-to-end-demo-script)
9. [Results and verification](#9-results-and-verification)
10. [Hosting and productionisation](#10-hosting-and-productionisation)
11. [Limitations and future work](#11-limitations-and-future-work)
12. [Appendix: tech stack inventory](#12-appendix-tech-stack-inventory)

---

## 1. Executive summary

**SentinelX I** is an end-to-end Cyber Threat Intelligence (CTI) platform.
A synthetic darknet forum is hosted as a Tor hidden service; a scraper pulls
its posts through the Tor network; each post is enriched by a four-stage
pipeline (regex + spaCy NER → 4-prompt local LLM → MITRE ATT&CK semantic
mapping); enrichment is persisted to SQLite; a FastAPI backend serves it
over REST + Server-Sent Events; and a React dashboard surfaces it as a
live timeline, an ATT&CK heatmap, an IOC pivot graph, lens-driven analyst
investigations, a case-file attack graph, and on-demand PDF reports.

The platform was built in eight stages over ~5 weeks. Every component runs
**locally on a single laptop with one consumer GPU (RTX 4060)**, with no
paid APIs, no SaaS subscriptions, and no commercial threat-intel feeds.
The end-to-end ingest-to-dashboard latency for a freshly posted darknet
thread is **30–60 seconds** during a live demo, of which ~95% is the LLM
forward pass; the remaining 235 historical posts in the demo corpus are
pre-enriched and visible immediately on dashboard load.

The system demonstrates the full CTI value chain that production tools
like MISP, Recorded Future, Mandiant Advantage, and Splunk SOAR implement
at enterprise scale, compressed onto a laptop, and built from explainable
open-source components rather than vendor magic.

---

## 2. Problem statement and motivation

### 2.1 What CTI analysts actually do

A real-world CTI analyst's day looks like this: they monitor unstructured
sources (darknet forums, paste sites, Telegram channels, public reports);
they extract **indicators of compromise** (IOCs — IP addresses, domains,
hashes, CVEs, crypto wallets, etc.); they map observed behaviours onto a
shared vocabulary, almost always **MITRE ATT&CK**; they fuse signals from
many posts into narratives ("this actor cluster is targeting Indian banks
with phishing kits"); and they deliver those narratives as
**reports** to stakeholders who don't have access to the underlying tools.

Doing this manually does not scale. The unstructured-text-to-actionable-
intelligence pipeline is the work that needs to be automated.

### 2.2 Why an end-to-end platform, not a single component

You can buy a darknet scraper, an NER model, an LLM API, an ATT&CK mapper,
and a report generator separately. The interesting engineering problem is
not any one of those — it is **the integration**: how they share state,
how the cursor logic prevents reprocessing, how the LLM's claims are
verified against a structured vocabulary instead of trusted blindly, and
how the analyst's investigation workflow loops back into the pipeline.

SentinelX I exists to make those integration choices visible and
defensible, end to end, in code that a single person can read in an
afternoon.

### 2.3 The "free, local, explainable" constraint

A second motivation: every component is chosen so the system runs without
recurring costs and without hidden inference behind an API. This was a
deliberate constraint:

- **Free:** Mistral 7B on Ollama replaces GPT-4 / Claude. spaCy
  `en_core_web_sm` replaces commercial NER. SQLite replaces hosted
  Postgres. The MITRE ATT&CK STIX export is a free public dataset.
- **Local:** every model weight, every database row, every scraped post
  lives on the operator's machine. No data leaves it.
- **Explainable:** every claim the system makes about a post is traceable
  to either a regex match, a spaCy span, an LLM prompt response, or a
  cosine similarity score against an ATT&CK technique embedding. No
  black-box "AI says so."

These three constraints shaped almost every implementation choice in the
sections below.

---

## 3. Methodology

### 3.1 Build sequence

The project was built in **eight stages**, each one's exit criteria being
end-to-end working code (not just compiling) plus a deep-dive document
recording the why-not-just-the-what:

| Stage | Component | Verifies |
|------:|-----------|----------|
| 1 | Synthetic .onion forum + Tor hidden service | Producer side of the pipeline |
| 2 | Tor scraper with cursor-based dedup | Consumer side of the pipeline |
| 3 | spaCy NER + regex IOC extraction | Structured-extraction layer |
| 4 | 4-prompt local-LLM enrichment chain (Mistral) | Unstructured-to-structured layer |
| 5 | MITRE ATT&CK ingest + semantic mapping | Tactical context layer |
| 6 | FastAPI backend (REST) | Service layer |
| 6.5 | Investigations + lenses + diagnostics | Analyst-workflow layer |
| 7 | React/Vite/Tailwind dashboard + SSE | Presentation layer |
| 8 | PDF export + case-file attack graph | Deliverable layer |

This order is **bottom-up**: nothing in stage N relies on something in
stage N+k. Each stage produces a deliverable verifiable in isolation
(a JSON dump, a CLI run, a curl response) before the next stage layers
on top.

### 3.2 Per-stage exit criteria

A stage was not considered complete until **all** of:

1. The code ran end-to-end (verified by execution, not just by `tsc` /
   `python -c "import …"` succeeding).
2. A `STAGE_XX_LEARN.md` document was written covering: what was built,
   how it works, why these choices over alternatives, the tech stack, and
   industry parallels. Audience: future maintainer who has never seen the
   code.
3. Manual verification of representative inputs: a real darknet post
   round-tripped through the new code. Concrete numbers (counts, latencies,
   token totals) recorded in the deep-dive.
4. Idempotency check: re-running the stage on the same data produced no
   new rows.

The deep-dives are preserved at [`docs/learn/`](learn/) and are the
authoritative per-stage documentation. This report summarises and
synthesises them; it does not replace them.

### 3.3 Verification ethos

Every stage's results were validated against the live database with
SQL aggregates rather than against a mock. Examples:

- Stage 3 verified by counting `ioc_type` distribution: 53 ipv4, 43 cve,
  36 btc, 21 domain, 14 email, 9 sha256 across 235 posts.
- Stage 4 verified by counting `intent` distribution: discussion=111,
  sale=92, other=17, recruitment=13, doxxing=2 — a plausible darknet mix
  rather than the LLM defaulting to one bucket.
- Stage 5 verified by counting `post_techniques.source` distribution:
  232 `llm_verified` + 45 `llm_unverified` + 15 `semantic` = 292 rows,
  with the top hits being T1566 (Phishing, 153), T1078 (Valid Accounts,
  34), T1086 (PowerShell, 14) — again, plausible darknet content.

Every count and every latency in this report is observed, not estimated.

---

## 4. System architecture

### 4.1 Block diagram

```
┌────────────────────────────────────┐
│  Synthetic .onion forum            │   Stage 1
│  Flask + gunicorn + SQLite (Docker)│
└──────────────┬─────────────────────┘
               │ HTTP via Tor SOCKS5 :9050
               ▼
┌────────────────────────────────────┐
│  Tor daemon (Docker)               │   Stage 1
└──────────────┬─────────────────────┘
               │
               ▼
┌────────────────────────────────────┐
│  Scraper                           │   Stage 2
│  httpx[socks] · cursor on          │
│  MAX(source_created_at)            │
└──────────────┬─────────────────────┘
               │ inserts → raw_posts
               ▼
┌────────────────────────────────────┐
│  Extraction pipeline               │   Stage 3
│  spaCy NER + regex IOCs            │
│  → iocs · entities                 │
└──────────────┬─────────────────────┘
               │
               ▼
┌────────────────────────────────────┐
│  Local-LLM chain                   │   Stage 4
│  Ollama / Mistral 7B               │
│  4 prompts: summary · intent ·     │
│   targets · techniques             │
│  → llm_analyses                    │
└──────────────┬─────────────────────┘
               │
               ▼
┌────────────────────────────────────┐
│  MITRE ATT&CK matcher              │   Stage 5
│  697 techniques · 384-d            │
│  MiniLM embeddings · cosine top-k  │
│  + LLM T-code verification         │
│  → post_techniques                 │
└──────────────┬─────────────────────┘
               │
               ▼
┌────────────────────────────────────┐
│  FastAPI backend                   │   Stages 6, 6.5
│  REST · SSE · PDF export ·         │
│  investigations + lenses           │
└──────────────┬─────────────────────┘
               │
               ▼
┌────────────────────────────────────┐
│  React/Vite/Tailwind dashboard     │   Stages 7, 8
│  Timeline · Heatmap · IOC pivot    │
│  Investigations · Case graph · PDF │
└────────────────────────────────────┘
```

### 4.2 Process model

The platform is **multi-process**, not multi-threaded. Each component is
its own OS process with its own SQLite connection:

- `sentinelx-forum` (Docker) — Flask + gunicorn
- `sentinelx-tor` (Docker) — Tor daemon
- `backend.scraper.run` (host Python) — only writes `raw_posts` and
  `scraper_runs`
- `backend.pipeline.run` (host Python) — only writes `iocs`, `entities`,
  `raw_posts.processed_at`, `extraction_runs`
- `backend.llm.run` (host Python) — only writes `llm_analyses`,
  `post_processing_state(stage='llm')`, `llm_runs`
- `backend.mitre.run` (host Python) — only writes `post_techniques`,
  `post_processing_state(stage='mitre')`, `mitre_runs`
- `uvicorn backend.api.main` (host Python) — read-only over the database
- `vite` / `npm run dev` (host Node) — read-only over the API

Every writer owns a disjoint set of tables. The API process never writes.
SQLite's WAL mode (default in modern Python) handles the multi-reader
+ one-writer-per-table contention with no explicit locking.

### 4.3 The cursor pattern

Each pipeline stage has its own **cursor** — an idempotency key that lets
re-running the stage produce no new work if the inputs haven't changed:

| Stage | Cursor expression |
|------:|--------|
| 2 (scrape) | `MAX(source_created_at)` over `raw_posts` |
| 3 (extract) | `WHERE processed_at IS NULL` over `raw_posts` |
| 4 (llm) | posts in `raw_posts.processed_at IS NOT NULL` AND not in `post_processing_state(stage='llm')` |
| 5 (mitre) | posts in `post_processing_state(stage='llm')` AND not in `post_processing_state(stage='mitre')` |

Stages 3, 4, 5 all use the `post_processing_state(raw_post_id, stage)`
table — the per-stage cursor pattern. Stage 3 is the exception that uses
the simpler `processed_at` column on `raw_posts`, kept for historical
reasons; everything later was migrated to the cursor table because
adding columns to `raw_posts` for every new stage doesn't scale.

This structure is what makes the demo robust: posting a new thread on the
.onion forum and running each `--once` does the right amount of work and
no more, every time, without the operator tracking state.

---

## 5. Component-by-component implementation

This section is a guided tour of each stage's code. For exhaustive design
rationale, see the matching `STAGE_XX_LEARN.md` in `docs/learn/`.

### 5.1 Stage 1 — Synthetic .onion forum + Tor stack

**Why a synthetic forum?** Real darknet forums have legal, ethical, and
operational issues we don't need: scraping copyrighted text, attribution
risk, takedown volatility, and (most importantly) the fact that what
makes a CTI tool *interesting* — IOCs, MITRE-mappable behaviour — is what
those forums are full of. Building our own forum lets us seed it with
controlled, plausible content (BTC wallets, CVEs, credential listings,
phishing how-tos) and verify the pipeline produces the right structured
output for known-correct inputs.

**Stack.** Flask + gunicorn for the web app; Jinja2 templates with a
deliberately darknet-ish CSS aesthetic; SQLite for thread/post storage;
seeded by `seed_data.py` which generates 60 threads / ~235 posts across
five categories (carding, ransomware, malware, exploits, services).
Hosted as a Tor hidden service: the official `torproject/tor` Docker
image, configured by `tor_config/torrc`, generating the `.onion` keypair
into a bind-mounted volume so the address survives container restarts.

**The bind-mount ownership gotcha.** Tor refuses to use a `HiddenServiceDir`
unless it's owned by the `debian-tor` user with mode 700. Bind-mounted
folders on Docker Desktop / Windows come into the container as
`root:root`. The fix is in `tor_config/entrypoint.sh`: the container starts
as root, `chown`s the mount to `debian-tor`, then `setpriv`s down to
`debian-tor` before exec'ing tor. The Dockerfile cannot use
`USER debian-tor` directly or the bind mount would break again.

**Verification.** `docker compose up --build -d` brings up both services;
`/healthz` returns 200; `tor_config/hidden_service/hostname` contains a
56-character v3 .onion address.

### 5.2 Stage 2 — Tor scraper

**Approach.** httpx with a SOCKS5 transport pointed at `127.0.0.1:9050`
(the Tor SOCKS port exposed by the Docker stack). Reads the `.onion`
hostname from the bind-mounted file. Calls the forum's `/api/threads` and
`/api/threads/{id}/posts` JSON endpoints (the forum exposes both HTML and
JSON). Persists every post as a row in `raw_posts` with a UNIQUE
constraint on `source_post_id` so re-scrapes are idempotent. Records each
run in `scraper_runs` for auditability.

**Cursor.** `MAX(source_created_at)` over `raw_posts`. Cannot drift.
Cannot duplicate (UNIQUE on `source_post_id` would reject anyway).
Robust against scraper crashes, against forum re-seeds, against
out-of-order posts (the `>` predicate handles them on a re-run).

**The `socks5://` vs `socks5h://` gotcha.** httpx 0.27 raises
`Unknown scheme for proxy URL` on `socks5h://`. The SOCKS5 transport in
`httpx[socks]` already does proxy-side hostname resolution by default,
which is what `.onion` addresses need. The "fix" of switching to
`socks5h://` is wrong; `socks5://` is correct.

**Verification.** First run: `fetched=235 inserted=235 duplicates=0`.
Second run: `fetched=0` (cursor working). All five forum categories
represented in the row distribution.

### 5.3 Stage 3 — Structured extraction (spaCy + regex)

**Approach.** Two passes per post:

1. **Regex IOC extractor** — an `IOC_PATTERNS` table covering ipv4, ipv6,
   cve, md5, sha1, sha256, btc (Bech32 + Base58), url, domain, email.
   Includes a defang/refang step that normalises `hxxp[:]//` → `http://`
   and `example[.]com` → `example.com` before matching, so analyst-
   defanged IOCs in posts still register.
2. **spaCy NER** with `en_core_web_sm`, parser disabled for speed
   (~30% throughput uplift, no semantic loss for our use). Keeps standard
   labels (PERSON / ORG / GPE / NORP / PRODUCT / EVENT / LOC) and adds
   custom keyword passes for `MALWARE` and `THREAT_ACTOR` (a hand-curated
   word list — small and explainable).

Both passes write into `iocs` and `entities` with `UNIQUE(raw_post_id,
ioc_type, value)` and `UNIQUE(raw_post_id, label, text)` respectively, so
re-extraction is safe. `raw_posts.processed_at` is set when both passes
complete; the cursor is `WHERE processed_at IS NULL`.

**Why regex for IOCs and NER for entities?** IOCs have rigid syntactic
shapes — an IPv4 is four octets, a CVE is `CVE-YYYY-NNNN+`. Regex hits
100% precision on a syntax-correct match. Named entities are
context-dependent (is "Apple" a company or a fruit?), so regex would
hallucinate; that's exactly what NER models are for.

**Verification.** Processed 235/235 posts → 176 IOCs (53 ipv4, 43 cve,
36 btc, 21 domain, 14 email, 9 sha256) + 239 entities (86 ORG, 50 PERSON,
24 NORP, 23 MALWARE, 21 PRODUCT, 20 GPE, 13 THREAT_ACTOR, 1 EVENT,
1 LOC). Second run = no-op (processed=0).

### 5.4 Stage 4 — Local-LLM enrichment chain

**Approach.** A 4-prompt sequential chain per post against `mistral:latest`
(7B parameters) running on Ollama:

1. `summary` — free-text 2–3 sentence summary.
2. `intent` — JSON with one of: `sale`, `recruitment`, `how-to`,
   `doxxing`, `discussion`, `other`.
3. `targets` — JSON: industries / geographies / victim_types lists.
4. `techniques` — JSON: MITRE T-codes + behaviour notes.

Each prompt is fed not just the post body (clipped at 4000 chars) but
also the **structured facts already extracted** in Stage 3 (IOCs +
entities), as a `KNOWN FACTS` block. This stops the LLM from re-deriving
IOC strings from the body, which it does badly — Mistral hallucinates IP
addresses and CVE numbers that look plausible but aren't in the post.
Treating Stage 3 as authoritative anchors the LLM to ground truth.

JSON outputs use Ollama's `format=json` mode plus a regex fallback in
`chain._extract_json` for the cases where Mistral wraps JSON in
explanatory prose. Idempotency: `llm_analyses` has `UNIQUE(raw_post_id)`
+ `ON CONFLICT DO UPDATE`. The cursor is `post_processing_state(stage='llm')`.

**The async lesson.** The original async refactor (`asyncio.gather` over
the 4 prompts plus `--concurrency 2` over posts) bought only **1.08x**
on a single-GPU 4060 — the model is compute-bound, not latency-bound.
Concurrency=4 was *worse* (~9s/post on a smoke sample) because in-flight
prompts starve each other. Honest result: async helps the single-post UX
during a live demo (per-post latency drops from ~8–12s to ~6–8s because
the 4 prompts overlap), but it does not give a 3–4× batch speedup.
The bottleneck is the GPU, not the Python event loop. Documented so
future maintainers don't re-attempt the optimisation.

**Verification.** Drained 235/235 posts in 1837s (~30.6 min, ~7.82 s/post).
Zero failures. Zero NULL fields. Intent distribution: discussion=111,
sale=92, other=17, recruitment=13, doxxing=2.

### 5.5 Stage 5 — MITRE ATT&CK mapping

**Two complementary paths per post:**

1. **LLM verification.** The Stage-4 `techniques_json` already contains
   T-codes. Each T-code is normalised (uppercase, strip whitespace) and
   checked against the local `mitre_techniques` corpus. If present, it
   becomes `source='llm_verified'`; if absent (LLM hallucinated a T-code
   that doesn't exist), `source='llm_unverified'`.
2. **Semantic discovery.** Independently of the LLM, embed the post body
   with `sentence-transformers/all-MiniLM-L6-v2` (384-d, L2-normalised so
   cosine ≡ dot product), compute `corpus_matrix @ post_vec`, take top-k
   above a threshold (defaults: k=5, threshold=0.45), and exclude any
   T-code already verified to avoid double-counting. These get
   `source='semantic'`.

This dual-path is the heart of the explainable-AI design. The LLM brings
**recall** (it can spot techniques the embedding misses); the embedding
brings **precision** (it can't hallucinate, and unverified LLM claims
get flagged as such instead of laundered into ground truth).

**Corpus.** Official MITRE Enterprise ATT&CK STIX 2.1 JSON (~36 MB),
filtered for non-revoked / non-deprecated `attack-pattern` objects with
an `mitre-attack` external_id. 697 techniques (including sub-techniques)
end up in `mitre_techniques`. Embeddings are stored as float32 BLOBs
alongside the model name for version tracking.

**Verification.** 697 techniques ingested + embedded in 45.0s (CPU).
235/235 posts matched in ~5s after model warm-up. Result: 232
`llm_verified` + 45 `llm_unverified` + 15 `semantic` = 292 mappings.
160/235 posts have ≥1 technique. Top hits: T1566 Phishing (153),
T1078 Valid Accounts (34), T1086 PowerShell (14).

### 5.6 Stage 6 — FastAPI backend

**Approach.** A single ~250-line `backend/api/main.py` exposing read-only
endpoints over the SQLite store. One sqlite3 connection per process via
FastAPI lifespan (`check_same_thread=False` because uvicorn runs sync
handlers in a threadpool; pipeline workers are the sole writers).

**Endpoints.**

- `GET /healthz` and `GET /healthz/full` (deep diagnostics — db, Tor SOCKS,
  Ollama, pipeline backlogs).
- `GET /stats` — totals + breakdowns in one round-trip.
- `GET /posts` — paginated list, filterable by category, intent,
  technique (joins `post_techniques`), or `q` (LIKE on body+title).
- `GET /posts/{id}` — full join: post + analysis (with `targets_json` and
  `techniques_json` deserialised) + iocs + entities + techniques.
  Techniques are LEFT-JOINed to `mitre_techniques` so `llm_unverified`
  rows survive (their technique_id has no row in the corpus).
- `GET /techniques`, `GET /techniques/{T-code}` — corpus browser.
- `GET /iocs`, `GET /entities` — aggregated by `(type, value)` /
  `(label, text)` with occurrence count + post-id list.
- `GET /events` — Server-Sent Events stream for the live timeline
  (see §5.8).

CORS is wide-open in dev (`allow_origins=["*"]`); production reads from
the `CORS_ORIGINS` env var.

### 5.7 Stage 6.5 — Investigations and analyst lenses

**Concept.** An *investigation* is a saved view: a name, a description, a
JSON filter (any combination of category / intent / technique / keyword /
ioc_type / time window / explicit post_ids), and an optional **lens** —
a system prompt that fuses the matched posts into a single narrative.

Four lenses ship: `threat_intel`, `ransomware`, `personal_identity`,
`corporate_espionage`. Each lens system prompt is rewritten to reference
SentinelX's structured fields directly: the rerun helper packs each post
as a block with body + IOCs + entities + MITRE techniques, then the lens
treats those structured fields as authoritative context (so the LLM
doesn't re-derive IOCs from text).

**Live-view semantics.** Filters are stored verbatim and re-evaluated on
every read. So an investigation with `{intent:'sale', q:'credential'}`
reflects the *current* corpus, not a snapshot from creation time. New
posts that match auto-appear; deleted posts disappear.

**Citations.** Lens summaries reference posts by `[#NNN]` markers (the
LLM is prompted to cite). The frontend renders these as clickable links
into the post detail panel; the PDF exporter rewrites them as numbered
footnote anchors (`[1]`, `[2]`, …) pointing at appendix sections.

**The filter SQL gotcha caught during build.** The original `_build_where`
draft used two `args.insert(0, ...)` calls for `technique` and `ioc_type`
JOIN bindings, which silently swapped the bindings when both filters
were set. Fix: maintain `join_args` and `where_args` as separate lists,
return `join_args + where_args` so `?` placeholders stay aligned with
their JOIN/WHERE order. The kind of bug that's invisible in casual
testing (single filters work fine) and only shows up with combined
filters.

### 5.8 Stage 7 — React frontend (vertical timecord, heatmap, IOC pivot, watch indicator)

**Stack.** Vite 6 + React 18 + TypeScript + Tailwind v4 (using `@theme`
tokens) + react-router-dom + TanStack Query + Framer Motion. Three.js
for the WebGL background. d3-force for graphs. EventSource for SSE.

**Visual identity.** A WebGL violet plasma shader (full-viewport plane
running domain-warped fbm noise) reacts to the cursor (flow pulled
toward pointer + violet halo) and clicks (expanding shockwave ring).
A 5-phase boot sequence (static → iris-open → world-map with pinging
.onion hub nodes → drips → glitch cut) plays once per session
(sessionStorage + module-scope flags handle React StrictMode's double-
mount). CRT/scanline + cursor halo + grain overlays add depth.

**Routes.**

- `/` — Home: hero + stats + feed teaser.
- `/posts` — vertical timecord timeline: one glowing thread top→bottom,
  posts branching alternately right/left as content cards. Filter chips,
  hover-to-see-techniques, click-to-open-DetailPanel, [REPLAY LIVE]
  button to re-fire the live-arrival animation on the newest post.
- `/techniques` — MITRE heatmap: 14 Enterprise tactics in 2 rows of 7,
  cells log-scaled by `post_count`.
- `/investigations` — left list / right detail / inline create form;
  CitationText component turns `[#NNN]` markers in summaries into
  clickable post links.
- `/iocs/:value` — IOC pivot graph: d3-force, central IOC + post
  satellites + co-occurring-IOC pivot chips below.
- `/investigations/:id/graph` — case-file attack graph (Stage 8, §5.9).

**SSE live timeline.** The backend's `GET /events` polls SQLite every 2s
for `id > since_id`, emits one `post` event per new row plus a 15s
keepalive `ping`. The bug fixed during build: the original handler used
`last = since_id or MAX(id)`, which Python's falsy-zero turned into
"start from the latest" when the timeline page connected with `since_id=0`.
Now it's `last = since_id` literally, with a separate `latest` for the
hello frame. Result: `since_id=0` correctly bootstraps the page with the
full historical stream, then keeps streaming new posts.

**Watch indicator.** A header-mounted polling component hits
`/healthz/full` every 12s, surfacing a status pill (IDLE / PROCESSING ·
N q / DEGRADED) and a hover popover with per-check status + per-stage
pending counts.

### 5.9 Stage 8 — PDF export and case-file attack graph

**PDF export** (`backend/api/export.py` + `GET /investigations/{id}/export`).
HTML+CSS rendered by **WeasyPrint**. Cover page (brand strip, title,
description, filter JSON in dark code block, lens summary with `[#NNN]`
rewritten as numbered footnote anchors via
`<sup class=cite><a href=#post-NNN>[k]</a></sup>`, MITRE coverage table
with `linear-gradient` width-bars). Each appendix page (one per cited
post; `page-break-before: always`) shows body + IOCs/entities in a 2-col
`columns: 2` grid + technique list with src-tagged pills colour-coded by
`llm_verified` / `semantic` / `llm_unverified`. Footnote ordering: order
of first `[#NNN]` appearance in summary, then any `summary_post_ids` not
yet seen so matched-but-uncited posts still appear in the appendix.

**Why HTML+CSS over ReportLab.** The dashboard already has a styled
visual identity in CSS. Reusing that paradigm for the PDF (rather than
rebuilding it programmatically with ReportLab primitives) keeps the
design effort in one place. WeasyPrint is the lightest "browser-grade"
HTML→PDF tool, depending only on the GTK 3 native runtime (Pango/Cairo)
rather than a full headless Chromium.

**The Windows GTK gotcha.** `OSError: cannot load library 'gobject-2.0-0'`
appears unless `C:\Program Files\GTK3-Runtime Win64\bin` is on PATH.
Documented in the README and in the endpoint's error handler (it catches
`OSError` separately and surfaces a clear hint). Also pinned `pydyf<0.11`
because pydyf 0.12 broke `weasyprint==62.3` with
`AttributeError: 'super' object has no attribute 'transform'`.

**Case-file attack graph.** A new route `/investigations/:id/graph`
(`frontend/src/pages/CaseGraph.tsx`) renders a single d3-force canvas
combining three node kinds for a given investigation: posts (purple
circle), IOCs (rotated square coloured by `ioc_type`), MITRE techniques
(amber square with T-code inside). Shared IOC/MITRE values dedupe to
**single nodes** with multiple links — that's the entire point of the
view: co-occurring artefacts pull their owning posts into clusters
automatically. Force-tuning: charge -240 (post) / -120 (artefact); link
distance 70 (ioc) / 90 (mitre); collide 22/14; alphaDecay 0.035
(~2.2s settle). Hover ego-graph highlight via a precomputed
`Map<id, Set<id>>` neighbour index — non-neighbours dim to 0.18 alpha,
edges thicken from 0.6→1.2 px. Click post → DetailPanel; click IOC →
`/iocs/:value`. Capped at MAX_POSTS=25 for legibility.

---

## 6. Data model

### 6.1 SQLite schema

```sql
-- Stage 2 — raw posts from the .onion forum
CREATE TABLE raw_posts (
  id                 INTEGER PRIMARY KEY,
  source_post_id     TEXT UNIQUE NOT NULL,        -- forum's own id
  thread_id          TEXT,
  thread_title       TEXT,
  category           TEXT,
  author             TEXT,
  body               TEXT NOT NULL,
  source_created_at  REAL NOT NULL,               -- unix epoch
  scraped_at         REAL NOT NULL,
  processed_at       REAL                          -- Stage 3 cursor
);
CREATE TABLE scraper_runs (id, started_at, finished_at,
  fetched, inserted, duplicates, status, error);

-- Stage 3 — structured extraction
CREATE TABLE iocs (
  id INTEGER PRIMARY KEY, raw_post_id INTEGER,
  ioc_type TEXT, value TEXT, span_start INT, span_end INT,
  UNIQUE(raw_post_id, ioc_type, value)
);
CREATE TABLE entities (
  id INTEGER PRIMARY KEY, raw_post_id INTEGER,
  label TEXT, text TEXT, span_start INT, span_end INT,
  UNIQUE(raw_post_id, label, text)
);
CREATE TABLE extraction_runs (id, started_at, finished_at,
  processed, iocs_found, entities_found, status);

-- Stage 4 — LLM enrichment
CREATE TABLE llm_analyses (
  raw_post_id INTEGER PRIMARY KEY,
  summary TEXT, intent TEXT,
  targets_json TEXT,        -- {industries, geographies, victim_types}
  techniques_json TEXT,     -- [{t_code, behaviour}, ...]
  model TEXT, generated_at REAL
);
CREATE TABLE llm_runs (id, started_at, finished_at, processed,
  failed, model, status);

-- Stages 4 & 5 — per-stage cursor
CREATE TABLE post_processing_state (
  raw_post_id INTEGER, stage TEXT, processed_at REAL,
  PRIMARY KEY(raw_post_id, stage)
);

-- Stage 5 — MITRE ATT&CK
CREATE TABLE mitre_techniques (
  technique_id TEXT PRIMARY KEY, name TEXT, description TEXT,
  tactics TEXT, url TEXT, is_subtechnique INT, parent_id TEXT,
  embedding BLOB,           -- float32 × 384, L2-normalised
  embedding_model TEXT
);
CREATE TABLE post_techniques (
  raw_post_id INTEGER, technique_id TEXT,
  source TEXT,             -- llm_verified | llm_unverified | semantic
  score REAL, evidence TEXT,
  UNIQUE(raw_post_id, technique_id, source)
);
CREATE TABLE mitre_runs (id, started_at, finished_at, processed,
  matched, status);

-- Stage 6.5 — investigations
CREATE TABLE investigations (
  id INTEGER PRIMARY KEY, name TEXT, description TEXT,
  filters_json TEXT, lens TEXT,
  summary TEXT, summary_model TEXT, summary_post_ids TEXT,
  created_at REAL, updated_at REAL, last_run_at REAL
);
CREATE INDEX idx_investigations_created ON investigations(created_at);
```

### 6.2 Why SQLite (and when it would not be enough)

For a single-laptop CTI demo with 235–10,000 posts, SQLite is the
ergonomically-correct choice: zero install, zero ops, ACID, full SQL,
fast for read-heavy workloads, single file to back up. The natural
upgrade path — should the corpus exceed ~1M posts or require concurrent
multi-writer access — is Postgres with the same schema, swapping the
SQLite-specific bits (the `executescript` migrations, the `ON CONFLICT
DO UPDATE` syntax) for their Postgres equivalents.

For embeddings, at current scale (697 techniques) a numpy float32 matrix
fits in a few MB and `corpus_matrix @ post_vec` is microsecond-fast. At
~50k+ vectors, you'd swap in a vector index (FAISS, ScaNN, or
pgvector / sqlite-vss) for O(log n) approximate nearest-neighbour rather
than O(n) brute force.

---

## 7. Design decisions and trade-offs

This section consolidates the load-bearing decisions made across the
project. Each one defends a specific trade-off.

### 7.1 Local LLM (Mistral 7B / Ollama) over a hosted API

**Trade-off.** Quality vs cost vs operability.

A hosted GPT-4 or Claude call would beat Mistral 7B on every quality
metric. But:

- Cost: even at 235 posts × 4 prompts × ~1500 tokens, a hosted API gets
  expensive; the project's "zero recurring cost" constraint rules it out.
- Privacy: real CTI work touches sensitive data. Many SOC operators
  cannot legally send forum content to a third-party API.
- Demonstrability: a judge can see the model file and the GPU it runs
  on. There is no "magic happens in someone else's datacentre."

Mistral 7B at q4 quantisation is *good enough* for the four prompts in
the chain because each prompt is small and well-scoped (intent picks
from 6 labels; techniques outputs a JSON list; etc.). Where the LLM
*would* fail (free-form analysis of long bodies), the system doesn't ask
it to.

### 7.2 LLM-verified + semantic-discovered MITRE mapping

**Trade-off.** Precision vs recall, with the LLM as a noisy oracle.

Treating the LLM's T-code list as ground truth would inherit its
hallucinations (the 45 `llm_unverified` rows in the corpus are LLM-
invented T-codes that don't exist in MITRE). Discarding the LLM and
relying on embeddings alone would lose the 45 cases where the LLM
*correctly* identified a technique that the embedding similarity score
missed.

The two-path design — verify the LLM's claims against the corpus, then
add embedding-discovered hits not already verified — keeps the LLM's
recall while bounding its precision damage. Every mapping is labelled
with its provenance (`source` column) so the dashboard and PDF can show
the analyst exactly how a technique tag landed on a post.

### 7.3 Per-stage cursor table over per-stage `processed_at` columns

**Trade-off.** Schema cleanliness vs migration simplicity.

Stage 3 added a `processed_at` column on `raw_posts` for its cursor.
Repeating that pattern would mean adding `extracted_at`, `llm_at`,
`mitre_at` columns to `raw_posts` for each new stage, plus future
columns for any stage we add later — `raw_posts` would grow indefinitely.

Stage 4 introduced `post_processing_state(raw_post_id, stage, processed_at)`
instead. Adding a new stage now means inserting rows with a new `stage`
value, no schema change. The cost: an extra JOIN per cursor query. For
a table with ~10k rows that cost is invisible.

### 7.4 SSE for the live timeline (vs WebSocket vs polling)

**Trade-off.** Implementation complexity vs subscriber semantics.

The timeline needs **server → client only**, with **automatic reconnect
on transient disconnect**, over **standard HTTP**. That is exactly what
EventSource / SSE provides. WebSocket would be heavier to implement on
both ends and reconnect-handling would be manual. Long-polling would
need explicit reconnect logic too.

The implementation polls SQLite every 2s for `id > since_id`. At
SentinelX scale (~one post every few seconds at peak), this is
invisible. Production-scale would either replace the poll with a real
pub-sub (Redis Streams, Postgres LISTEN/NOTIFY) or move the API process
to share an in-memory event channel with the writers; neither is
necessary here.

### 7.5 d3-force for graphs (vs Cytoscape, vis-network)

**Trade-off.** Bundle size vs graph-layout features.

We have at most ~25 posts × ~5–10 IOCs × ~4 techniques = ~150 nodes per
view. d3-force settles such a graph in <2 seconds and weighs ~10 KB.
Cytoscape (~400 KB) and vis-network (~200 KB) carry features we don't
use (multi-graph layouts, edge bundling, animations beyond what
Framer Motion already provides). For graphs of >500 nodes the trade-off
inverts; we are not near that scale.

### 7.6 HTML+CSS → PDF via WeasyPrint (vs ReportLab vs headless Chromium)

**Trade-off.** Visual fidelity vs deployment weight vs design effort.

ReportLab is pure Python (no native deps) but every styled element is
manual `canvas.drawString(x, y)` — unbearable to maintain for anything
beyond a printout. Headless Chromium gives perfect fidelity but ships a
~150 MB browser. WeasyPrint sits in the middle: depends on Pango/Cairo
(GTK runtime, ~30 MB), supports modern CSS (page rules, multi-column
layout, gradients), and lets the PDF reuse the dashboard's design
language. For a single-tenant offline PDF generator, WeasyPrint is the
right tier.

### 7.7 Lens summaries live, not snapshotted

**Trade-off.** Predictability vs freshness.

A snapshotted investigation summary is reproducible — re-rendering it
produces the same text. A live one reflects the current corpus, so a
new matching post immediately changes what the rerun produces.

For a CTI tool, freshness wins. The whole value of an investigation is
that "all sale posts mentioning credentials" stays a meaningful
specification across days/weeks. The reproducibility requirement is
served by the **PDF export**: once exported, the PDF is immutable. The
dashboard view is the live one; the PDF is the reproducible artefact.

### 7.8 Citation footnotes (`[#NNN]` → `[1]`) over raw inline references

**Trade-off.** Print-readability vs source-locality.

`[#NNN]` is great in the dashboard (clickable, opens a panel). In a
printed PDF those raw post-id markers look like debug output. The
exporter rewrites them as Wikipedia-style numbered footnotes that
hyperlink (in PDF viewers that support it) to the matching appendix
section. Print-friendly, viewer-interactive, source-localised.

### 7.9 Synthetic forum vs real darknet scrape

**Trade-off.** Demonstrability vs realism.

A live darknet scrape brings legal/ethical baggage (copyright,
jurisdictional, attribution) that have no place in a learning project.
A synthetic forum lets us seed *known* IOCs and behaviours so we can
verify the pipeline produces the right structured output for known-
correct inputs — the hardest thing about a real scrape is precisely that
you have no ground truth.

The synthetic forum is hosted as a real Tor hidden service, scraped
through a real Tor SOCKS5 circuit. The transport, the dedup, the
Tor-specific concerns are all exercised end-to-end. Only the *content*
is synthetic.

---

## 8. End-to-end demo script

This is the live demo flow for the judging panel. Total runtime ~5–7
minutes. Assumes the public Vercel + Render deployment is up and the
laptop has Docker, Python venv, Ollama, and the frontend dev server.

### 8.1 Pre-demo setup (do this 10 minutes before)

```powershell
# 1. Ensure GTK PATH (PDF export)
$env:PATH = "C:\Program Files\GTK3-Runtime Win64\bin;$env:PATH"

# 2. Bring up Docker stack (Tor + .onion forum)
docker compose up -d

# 3. Verify Ollama is running with mistral pulled
ollama list   # should show mistral:latest

# 4. Pre-warm the Render free-tier (~30-60s cold start)
curl https://YOUR-RENDER-URL.onrender.com/healthz

# 5. Start the local API for the live ingestion demo
backend/.venv/Scripts/python.exe -m uvicorn backend.api.main:app --port 8765
```

### 8.2 The narrative (read this verbatim, with the screen)

**Step 1 — open the public dashboard.** Open
`https://sentinelx.vercel.app` in a browser tab. The boot sequence plays.
Land on Home: 235 posts, 235 LLM-analysed, 235 MITRE-matched, 176 IOCs,
239 entities, 697-technique corpus. Point at the WatchIndicator: "the
backend is alive on Render — these stats are coming from the live API."

**Step 2 — show the timeline.** Click `/posts`. The vertical violet
thread runs top→bottom, posts branching alternately right/left as cards.
Filter to `WITH CVE`: only posts containing CVEs remain. Hover one:
top-6 MITRE techniques fan out as a halo. Click it: the DetailPanel
slides in with the full body, structured analysis, IOCs, entities,
techniques. Close it. Filter back to `ALL`.

**Step 3 — show the heatmap.** Click `/techniques`. 14 ATT&CK tactics in
two rows. Cells shade by post_count. Click one: a side panel lists every
technique under that tactic with its post count. Click a technique: the
posts mapped to it. Close.

**Step 4 — show an investigation with a lens summary.** Click
`/investigations`. The "fraud" investigation. Its lens summary references
posts as `[1]`, `[2]`, `[3]` — those are the citation footnotes. Click
one: DetailPanel opens to that exact post. Close.

**Step 5 — case-file attack graph.** Click `[ CASE GRAPH ]` on the
investigation. The d3-force canvas settles: posts in the centre as
purple dots, their IOCs as cyan diamonds, their MITRE techniques as
amber squares. Hover a post: only its neighbours stay bright; everything
else dims. Show how the same IOC node is connected to multiple posts —
"this IOC is the cluster signal."

**Step 6 — PDF export.** Click `[ EXPORT PDF ]`. New tab opens with the
PDF. Cover page with metadata, lens summary with `[1]/[2]/[3]` footnotes,
MITRE coverage chart with gradient bars, then one appendix page per
cited post. "This is the deliverable an analyst hands to a stakeholder
who doesn't have access to the dashboard."

**Step 7 — switch to your laptop.** Open the local Tor Browser. Show the
synthetic .onion forum at the `.onion` address. Pick a category. Say:
"This is a real Tor hidden service; SentinelX is talking to it through
the Tor SOCKS proxy on my laptop."

**Step 8 — post a new thread live.** Click `[NEW THREAD]` in the forum.
Title: `LIVE DEMO: 0day in Cisco IOS XE — selling`. Body: a short
plausible darknet sale post with a BTC wallet, a CVE, an IP, a domain.
Submit.

**Step 9 — run the four pipeline stages.** In a terminal:

```powershell
backend\.venv\Scripts\python.exe -m backend.scraper.run --once
backend\.venv\Scripts\python.exe -m backend.pipeline.run --once
backend\.venv\Scripts\python.exe -m backend.llm.run --once
backend\.venv\Scripts\python.exe -m backend.mitre.run --once
```

Talk through each: scraper pulls through Tor, extractor runs spaCy+regex,
LLM chain does 4 prompts on Mistral, MITRE matcher does verify + semantic
top-k. Total ~30–45 seconds.

**Step 10 — refresh the dashboard.** Go back to `/posts`. The new post
appears at the top with full enrichment: intent, IOCs (BTC + IP +
domain), MITRE techniques (T1566 / T1078 / T1059, etc.). Click it →
DetailPanel shows the full LLM summary. Done.

### 8.3 Talking points for the judge

- "Every claim the system makes is traceable: regex match, spaCy span,
  LLM prompt response, or cosine similarity. There's no black box."
- "The `llm_verified` / `llm_unverified` / `semantic` distinction is
  the precision-vs-recall trade-off made explicit in the data model."
- "The forum is synthetic, but the Tor circuit is real. Same code paths
  would scrape a real .onion."
- "Total cost to run: zero. No API keys, no subscriptions. Mistral
  weights are local."
- "The PDF you see is generated on demand from the same database the
  dashboard reads — no separate report-generation pipeline."

---

## 9. Results and verification

### 9.1 Pipeline metrics on the demo corpus (235 posts, RTX 4060)

| Stage | Throughput | Output |
|------|------------|--------|
| 2 — Scraper | 235 posts / first run, 0 / second run | Cursor proven |
| 3 — Extraction | 235 / 235 in seconds | 176 IOCs + 239 entities |
| 4 — LLM (sequential) | 1979s (~8.42 s/post) | 235 / 235, 0 NULLs |
| 4 — LLM (async) | 1837s (~7.82 s/post) | 1.08× speedup, GPU-bound |
| 5 — MITRE ingest | 45.0s for 697 techniques | Embeddings cached |
| 5 — MITRE match | 235 in ~5s after warm-up | 232 verified + 45 unverified + 15 semantic |
| 6 — API cold start | <1s | All endpoints responsive |
| 6.5 — Lens rerun | 36.78s on 6-post matched set | 1744-char summary |
| 8 — PDF export | <1s | 156 KB for inv #2 |

### 9.2 Distribution sanity-checks

- Intent: discussion=111, sale=92, other=17, recruitment=13, doxxing=2.
  Plausible darknet mix.
- IOC types: ipv4=53, cve=43, btc=36, domain=21, email=14, sha256=9.
  No type dominates, all six core types appear.
- Top MITRE: T1566 Phishing (153), T1078 Valid Accounts (34), T1086
  PowerShell (14). The classic darknet trio.

### 9.3 Idempotency

Every pipeline stage's `--once` is a no-op on the second run when no new
input has arrived (verified by `processed=0` lines in each stage's
output). End-to-end re-run after a fresh scrape inserts only the new
posts' enrichment, never touching prior rows.

---

## 10. Hosting and productionisation

### 10.1 The two-tier hosting plan

For a public demo without paying for GPU/Tor capacity, the cleanest
split is:

| Component | Host | Why |
|-----------|------|-----|
| React frontend | **Vercel** (free) | Static SPA, global CDN, zero config |
| FastAPI backend + SQLite | **Render** (free) | One-click `render.yaml`, ships the DB along with the deploy |
| Ollama + Mistral | **Demo laptop** | No free GPU hosting exists |
| Tor + .onion forum | **Demo laptop** | Free PaaS providers ban Tor |
| Live LLM rerun + scraping | **Demo laptop** | Both depend on the above |

The Vercel-hosted dashboard talks to the Render backend over HTTPS. The
public dashboard works fully for the 235 pre-enriched posts (timeline,
heatmap, IOC pivot, investigations, case graph, PDF export). The live-
ingestion demo (steps 7–10 of §8.2) runs against the *laptop's* local
backend on `:8765`. During the live ingestion segment of the demo, the
operator can either temporarily point the public dashboard at the local
backend with an env override, or just show the local dashboard at
`http://localhost:5173` for that segment.

### 10.2 Step-by-step deployment

#### A. Backend — Render

1. Push the repo to GitHub.
2. Sign in to [render.com](https://render.com), choose "New > Blueprint",
   point at the GitHub repo. Render auto-detects `render.yaml`.
3. The single env var that needs setting is `CORS_ORIGINS`. Defer this
   until step C — set it once you know the Vercel URL.
4. Hit "Apply". First deploy takes ~5 minutes (pip install + cold start).
5. Note the URL Render assigns, e.g. `https://sentinelx-api.onrender.com`.
6. Verify: `curl https://sentinelx-api.onrender.com/healthz` returns
   `{"status":"ok"}`. `curl …/stats` returns the 235-post numbers.

**Free-tier caveat.** Render's free tier idles the service after 15 min
of no traffic. First request after idle takes ~30–60s. Pre-warm by
hitting `/healthz` from your laptop right before showing the dashboard
to a judge.

**PDF export caveat.** Render's Python runtime image does **not** include
the GTK 3 runtime, so the PDF endpoint will return 500 in production.
For a judge demo, the PDF works on the **localhost** demo path and the
Vercel dashboard's `[ EXPORT PDF ]` button works only when you're
pointing it at the local backend. Productionising would mean swapping
`runtime: python` for a Docker image with GTK installed; that's a
30-minute change but out of scope for the free tier.

#### B. Frontend — Vercel

1. Sign in to [vercel.com](https://vercel.com), "Add New Project", pick
   the same GitHub repo.
2. **Root directory:** `frontend/` (Vercel auto-detects Vite from the
   subdirectory).
3. **Environment variable:** `VITE_API_BASE` = the Render URL from step A6.
4. Hit "Deploy". First build takes ~2 minutes.
5. Vercel assigns a URL, e.g. `https://sentinelx.vercel.app`.

#### C. Wire the two together

1. Back in Render, set the `CORS_ORIGINS` env var to your Vercel URL
   (e.g. `https://sentinelx.vercel.app`). Save → Render redeploys.
2. Open the Vercel URL. The dashboard should load with the 235 posts.
3. Sanity-check the WatchIndicator: it should show DEGRADED in
   production because Tor/Ollama probes fail (they're not on Render).
   This is correct behaviour — explain it to the judge as the system's
   honest reporting that the live-ingestion stack is laptop-only.

#### D. The local-only demo path

For steps 7–10 of the demo script, you'll run the four pipeline stages
against the **local** backend on `:8765`. If you want the public Vercel
dashboard to reflect the live-ingestion result during the demo, the
cleanest move is to temporarily set Vercel's `VITE_API_BASE` to a
tunnelled localhost (ngrok / cloudflared) for the demo session — but
the simpler path is to switch tabs to `http://localhost:5173` for the
live-ingestion segment.

### 10.3 What productionising would require

The "free-tier-friendly" architecture above ships a working public demo,
but a real productionised SentinelX would need:

- **Containerised backend with GTK** so PDF export works in production.
  ~30 min to write a Dockerfile that installs `libpango-1.0-0` etc.
- **Postgres** instead of SQLite once the corpus exceeds a few hundred
  thousand posts. The schema ports almost verbatim.
- **Vector index** (FAISS, pgvector, sqlite-vss) once the MITRE corpus
  exceeds ~50k entries. The current 697 entries don't justify it.
- **Redis Streams** or Postgres `LISTEN/NOTIFY` for the SSE channel
  instead of polling.
- **GPU service split.** Ollama on a dedicated GPU node (RunPod, Lambda
  Labs, on-prem); the FastAPI process stays stateless and cheap.
- **Auth and tenancy.** Currently there is none — the dashboard is a
  single-tenant demo. Real CTI deployments are multi-tenant with
  per-team RBAC.
- **A real ingestion source.** The synthetic forum would be replaced or
  augmented by RSS/Atom feeds from CERTs, MISP correlations, public
  OSINT trackers. The scraper layer is already abstracted enough that
  adding new sources is a per-source `Scraper` subclass.

None of these are required for the demo. They are the obvious next
moves if SentinelX I were to grow into SentinelX II.

---

## 11. Limitations and future work

### 11.1 Honest limitations

- **Mistral 7B is the cheapest defensible local LLM, not a great one.**
  A 13B / 70B model would produce noticeably better summaries and more
  reliable T-code identification. The 4-prompt chain is structured to
  minimise the impact (each prompt is small and well-scoped), but a
  bigger model would still help.
- **The .onion forum is synthetic.** A real darknet scrape would expose
  failure modes the demo doesn't (Cloudflare-style anti-bot, login
  walls, paywalled threads, intentional poison data). The scraper code
  is realistic enough that adding a real source would mostly be writing
  a new `Scraper` subclass; what's missing is the forum-specific HTML
  parsing.
- **The MITRE corpus is Enterprise only.** Mobile and ICS ATT&CK
  matrices are not ingested. For a realistic CTI tool, both would
  matter.
- **Production PDF export needs a Docker backend.** The free Render
  Python runtime can't render PDFs.
- **No auth, no tenancy, no audit log.** Single-tenant demo.
- **The 235-post corpus is small enough that simple SQL aggregates
  outperform any clever indexing.** That makes the implementation
  small but means scaling stories (vector indexes, query planners,
  cache layers) are unexercised.

### 11.2 Concrete future work

In rough priority order:

1. **Streaming summary prompt** — for the live demo, render the LLM
   summary token-by-token in the DetailPanel as it generates. Already
   supported by Ollama; just needs SSE wiring on the frontend.
2. **Per-technique page** at `/techniques/:T-code` — the heatmap drills
   down by tactic but not by individual technique. The data is there.
3. **Real ingestion source** — point the scraper at a public CERT RSS
   feed in addition to the synthetic forum. Demonstrates multi-source
   ingestion.
4. **PDF queue worker** — move PDF rendering to RQ / Celery so a 200-
   page report doesn't block uvicorn.
5. **Auth** — at minimum, JWT with a single-admin user, so the public
   dashboard can be made write-protected for new investigations.
6. **MISP export** — the structured records (IOCs, entities, ATT&CK
   tags) map cleanly onto MISP's attribute model. A one-direction
   export would let SentinelX feed an existing MISP install.
7. **Risk scoring** — combine intent + technique + IOC types into a
   per-post severity score, sorted into the timeline.

---

## 12. Appendix: tech stack inventory

### 12.1 Languages and runtimes

| Language | Where | Reason |
|----------|-------|--------|
| Python 3.12 | scraper, pipeline, LLM, MITRE, API, PDF | Strongest ML/NLP ecosystem, FastAPI's home |
| TypeScript | frontend | Static guarantees on a 1000+ line React app |
| HTML/CSS | dashboard, PDF | Reusable across rendering paradigms |
| SQL (SQLite dialect) | every layer | Schema is the contract |
| Bash + PowerShell | dev orchestration | Cross-platform reality on Windows |

### 12.2 Libraries by stage

**Scraper (Stage 2):** `httpx[socks]==0.27.2` (HTTP + SOCKS5 transport).

**Extraction (Stage 3):** `spacy==3.7.5` + `en_core_web_sm` (NER); custom
regex IOC extractor (no library).

**LLM (Stage 4):** **Ollama** (binary, separate process) running
`mistral:latest`. Python client is a thin custom wrapper over Ollama's
HTTP API.

**MITRE (Stage 5):** `sentence-transformers==5.4.1` +
`all-MiniLM-L6-v2` (384-d embeddings); `numpy==1.26.4` for the dot-product
matrix. Official MITRE Enterprise ATT&CK STIX 2.1 JSON export.

**Backend (Stage 6 + 6.5):** `fastapi==0.115.0` +
`uvicorn[standard]==0.30.6` (ASGI server); built-in `sqlite3`.

**Frontend (Stage 7):** Vite 6, React 18, TypeScript, Tailwind v4,
react-router-dom v6, `@tanstack/react-query`, `framer-motion`, `three`,
`d3-force`. EventSource (browser native) for SSE.

**PDF (Stage 8):** `weasyprint==62.3` + `pydyf<0.11` (PDF backend pin).
GTK 3 runtime (system-native).

### 12.3 Industry parallels

For each major component, the closest commercial / open-source analogue:

| SentinelX component | Real-world analogue |
|---------------------|---------------------|
| Tor scraper + dedup | RecordedFuture Diamond Threat Intel, DarkOwl Vision |
| spaCy + regex extraction | OpenCTI extraction pipeline, ThreatConnect parsers |
| Local-LLM enrichment chain | Microsoft Security Copilot, Mandiant AI Insights |
| MITRE ATT&CK semantic mapping | DeTT&CT, ATT&CK Navigator, Atomic Red Team mapping |
| FastAPI + SQLite | OpenCTI core, MISP backend |
| Investigations + lenses | TheHive cases, Splunk SOAR cases |
| Dashboard + heatmap | OpenCTI UI, Maltego, Splunk SOAR investigation view |
| IOC pivot + case graph | Maltego transforms, Splunk SOAR graph view |
| PDF report export | MISP report, Mandiant intel briefs, Recorded Future PDFs |

The goal of SentinelX I is not to compete with any of these — it's to
demonstrate that the full vertical stack can be built understandably,
locally, and cheaply, by one person, in code that fits in a single repo.

---

**End of report.**
