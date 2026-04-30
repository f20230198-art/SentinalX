# CLAUDE.md — Session Primer for SentinelX I

> **You are reading this because Claude Code auto-loads `CLAUDE.md` at session start.**
> This file is the project's source of truth for "what is this project, where are we, what comes next." Read it top to bottom before doing anything else, then read `PROGRESS.md` for the stage-by-stage history.

---

## 1. What this project is

**SentinelX I** is an end-to-end Cyber Threat Intelligence (CTI) platform built as a learning project. Pipeline:

```
synthetic .onion forum  →  Tor scraper  →  NER + IOC extraction  →
local LLM (Mistral via Ollama)  →  MITRE ATT&CK mapping  →
SQLite store  →  FastAPI backend  →  React/Vite/Tailwind frontend
                                    →  WeasyPrint PDF export
                                    →  vis.js attack graph
```

Full background, design rationale, and glossary live in [`TECHNICAL_PRIMER.md`](TECHNICAL_PRIMER.md). Read that if any term here is unclear.

---

## 1.5 Working efficiency (READ FIRST)

Finish work the most token-efficient way possible. Don't read whole files when a grep suffices, don't re-read files already in context, batch independent tool calls in parallel, keep prose terse, no preamble/summary fluff. Apply this to every task in this repo.

## 2. Who the user is and how they want to work

- **User:** Srivathsa (Windows 11, OneDrive-synced repo, bash + PowerShell available, Docker Desktop installed, Python 3.13 + 3.12 + 3.11 installed).
- **Learning style — this is critical:** *Build first, teach after.* For each of the 8 stages:
  1. Implement the working code end-to-end without pausing to coach mid-flight.
  2. Then write **one** exhaustive `STAGE_XX_LEARN.md` at the repo root covering: what was built, how it works, why these choices over alternatives, the tech stack, and how each piece is used in real industry.
  3. The user reads the LEARN doc on their own time. **Do not** interrupt the build with "do you understand X before we continue?" comprehension checks. That mode was explicitly rejected.
- **Genuine prerequisite blockers** (e.g. missing tool install) are still fine to pause for — those aren't pedagogical pauses.
- **Tone:** terse status lines while building. Save long explanations for the LEARN doc.

This preference is also persisted in user-level memory (`feedback_learning_style.md`), but state it here too so future Claudes can see it without memory access.

---

## 3. Project status — keep this section current

**Last updated:** 2026-04-30 (**Stage 7 scaffolded.** Vite 6 + React 18 + TS + Tailwind v4 in `frontend/`. Violet palette (`#0A0612` base / `#13033b` deep / `#A78BFA` accent) wired via `@theme` block in `src/index.css`. Boot sequence + CRT toggle + cursor halo + scramble hook implemented. Home page renders hero, health grid, post-feed teaser, and top-techniques bar chart against the live Stage-6 API via `/api/*` proxy. Type-checks clean, dev server verified end-to-end. Routes for `/posts`, `/techniques`, `/investigations` are placeholders — those land in subsequent commits along with the horizontal Dark-style timeline. `STAGE_07_DESIGN.md` decisions locked.)

### Stages (8 total)

| # | Name                              | Status        | LEARN doc                |
|---|-----------------------------------|---------------|--------------------------|
| 1 | Synthetic .onion forum + Tor      | ✅ complete   | ✅ `STAGE_01_LEARN.md`    |
| 2 | Tor scraper + dedup + raw_posts   | ✅ complete   | ✅ `STAGE_02_LEARN.md`    |
| 3 | NER + IOC extraction (spaCy + regex) | ✅ complete   | ✅ `STAGE_03_LEARN.md`    |
| 4 | Local LLM pipeline (Ollama/Mistral, 4 stages) | ✅ complete   | ✅ `STAGE_04_LEARN.md`    |
| 5 | MITRE ATT&CK ingest + vector index | ✅ complete   | ✅ `STAGE_05_LEARN.md`    |
| 6 | FastAPI backend                   | ✅ complete   | ✅ `STAGE_06_LEARN.md`    |
| 6.5 | Investigations + lenses + diagnostics | ✅ complete | ✅ `STAGE_06_5_LEARN.md` |
| 7 | React/Vite/Tailwind frontend      | ⬜             | — (design: `STAGE_07_DESIGN.md`) |
| 8 | PDF export + attack graph polish  | ⬜             | —                        |

### What is currently running / verified working

- **Stage 1 stack** (`docker compose up --build -d` from repo root):
  - `sentinelx-forum` — Flask + gunicorn on `:5000` (internal). Seeds 60 threads / ~235 posts on first boot. `/healthz` 200.
  - `sentinelx-tor` — Tor bootstrapped 100%, SOCKS5 on host `127.0.0.1:9050`, hidden service at `tor_config/hidden_service/hostname` (gitignored, persists across `down -v`).
- **Stage 2 scraper** (`backend/.venv/Scripts/python.exe -m backend.scraper.run --once`):
  - Reads `.onion` from `tor_config/hidden_service/hostname`.
  - httpx with SOCKS5 transport via `socks5://127.0.0.1:9050` (proxy-side DNS is the default in httpx[socks] — do **not** use `socks5h://`, see Gotcha #7).
  - Persists to `backend/db/sentinelx.db` → `raw_posts` (UNIQUE on `source_post_id`) + `scraper_runs` log.
  - Cursor = `MAX(source_created_at)` over `raw_posts`. Cannot drift.
  - CLI: `--once` (default), `--watch --interval N`, `--reset-cursor`.
  - **Verified 2026-04-27:** first run fetched=235 inserted=235 duplicates=0; second run fetched=0 (cursor working); rows distributed across all 5 forum categories.
- **Stage 3 extraction pipeline** (`backend/.venv/Scripts/python.exe -m backend.pipeline.run --once`):
  - spaCy `en_core_web_sm` (parser disabled for speed) + regex IOC extractor with defang/refang.
  - IOC types: ipv4, ipv6, cve, md5, sha1, sha256, btc, url, domain, email.
  - Entity labels kept: PERSON/ORG/GPE/NORP/PRODUCT/EVENT/LOC + custom MALWARE and THREAT_ACTOR keyword passes.
  - Schema additions: `iocs`, `entities`, `extraction_runs` tables; `raw_posts.processed_at` column (added via guarded `ALTER TABLE` in `Store._init_schema` since SQLite has no `IF NOT EXISTS` for ADD COLUMN).
  - Cursor = `WHERE processed_at IS NULL` over `raw_posts` (Stage 3 owns its own cursor — does NOT share Stage 2's).
  - Idempotent: `UNIQUE(raw_post_id, ioc_type, value)` and `UNIQUE(raw_post_id, label, text)` guarantee re-extraction is safe.
  - CLI: `--once` (drains all unprocessed; default), `--watch --interval N`, `--reset` (wipes extractions + clears `processed_at`).
  - **Verified 2026-04-27:** processed 235/235 posts → 176 IOCs (53 ipv4, 43 cve, 36 btc, 21 domain, 14 email, 9 sha256) + 239 entities (86 ORG, 50 PERSON, 24 NORP, 23 MALWARE, 21 PRODUCT, 20 GPE, 13 THREAT_ACTOR, 1 EVENT, 1 LOC). Second run = no-op (processed=0).
- **Stage 4 LLM pipeline** (`backend/.venv/Scripts/python.exe -m backend.llm.run --once`):
  - Local Ollama (host port 11434), `mistral:latest` 7B running 100% on the user's RTX 4060 (~5.1 GB VRAM).
  - 4-prompt chain per post: `summary` (free text) → `intent` (JSON, one of sale/recruitment/how-to/doxxing/discussion/other) → `targets` (JSON: industries/geographies/victim_types) → `techniques` (JSON: MITRE T-codes + behaviour notes). Each prompt receives the post body **plus** Stage-3 IOCs/entities as a `KNOWN FACTS` block so the LLM doesn't re-derive what we already have. Body clipped to 4000 chars; JSON outputs use Ollama `format=json` plus a fallback `{...}` regex extractor in `chain._extract_json` for the cases Mistral wraps JSON in prose.
  - Schema additions: `llm_analyses` (one row per post, `UNIQUE(raw_post_id)`, `ON CONFLICT DO UPDATE`), `post_processing_state(raw_post_id, stage, processed_at)` (per-stage cursor table — replaces the "extra column on raw_posts" pattern; Stage 5+ will reuse it), `llm_runs` audit log.
  - Cursor: posts where `raw_posts.processed_at IS NOT NULL` (Stage 3 done) AND not yet present in `post_processing_state` for stage `'llm'`. Stage 4 owns its own cursor — does NOT share Stage 3's `processed_at`.
  - CLI: `--once` (default, drains until partial batch), `--watch --interval N`, `--reset` (drops llm tables + clears stage='llm' rows). `--limit N` for smoke tests; `--model NAME` to swap models; `--batch N` for `llm_runs` row granularity.
  - **Verified 2026-04-29 (sync, sequential prompts):** drained 235/235 posts in 1979s (~33.0 min, ~8.42s/post on RTX 4060). Zero failures, zero NULL fields. Intent distribution: discussion=111, sale=92, other=17, recruitment=13, doxxing=2.
  - **Verified 2026-04-29 (async, concurrency=2 + 4-prompt fanout via `asyncio.gather`):** drained 235/235 posts in 1837s (~30.6 min, ~7.82s/post). **Honest result: 1.08x speedup.** Reason: a 7B model on a single 4060 is compute-bound, not latency-bound — there is no idle GPU for parallel requests to fill. Concurrency=4 was actually *worse* (~9s/post on 8-post sample) because too many in-flight prompts starve each other. The async refactor still helps the **live `--watch` demo path** (per-post latency drops from ~8-12s to ~6-8s because the 4 prompts within one post overlap), but it's not the 3-4x batch speedup originally hoped for. Defaults: `--concurrency 2` (set in `run.py`).

- **Stage 5 MITRE pipeline** (`backend/.venv/Scripts/python.exe -m backend.mitre.run --ingest` then `... --once`):
  - Corpus: official MITRE Enterprise ATT&CK STIX 2.1 JSON, cached at `data/mitre/enterprise-attack.json` (~36 MB, gitignored). Filters revoked/deprecated, keeps `attack-pattern` objects with an `mitre-attack` external_id. 697 techniques (incl. sub-techniques) end up in `mitre_techniques`.
  - Embedder: `sentence-transformers/all-MiniLM-L6-v2` (384-d, L2-normalised → cosine == dot product). Stored as float32 BLOB in `mitre_techniques.embedding`, alongside `embedding_model` for version tracking.
  - Match path per post: (1) LLM verification — parse `llm_analyses.techniques_json`, normalise T-codes, mark each `llm_verified` if in corpus else `llm_unverified`. (2) Semantic discovery — embed post body, `corpus_matrix @ post_vec` for cosine scores, take top-k with score ≥ threshold, exclude T-codes already verified to avoid double-counting.
  - Cursor: posts with `post_processing_state(stage='llm')` AND no `post_processing_state(stage='mitre')` row. Stage 5 owns its own cursor.
  - Schema additions: `mitre_techniques`, `post_techniques (UNIQUE(raw_post_id, technique_id, source))`, `mitre_runs`.
  - CLI: `--ingest`, `--once`/`--watch`/`--reset`/`--reset-corpus`, `--topk 5` (default), `--threshold 0.45` (default), `--model NAME`, `--limit N` for smoke tests.
  - **Verified 2026-04-30:** 697 techniques ingested and embedded in 45.0s (CPU). 235/235 posts matched in ~5s after model warm-up. Result: 232 llm_verified + 45 llm_unverified + 15 semantic = 292 rows. 160/235 posts have ≥1 technique. Top hits: T1566 Phishing (153), T1078 Valid Accounts (34), T1086 PowerShell-legacy (14). Re-run is no-op.

- **Stage 6 FastAPI backend** (`backend/.venv/Scripts/python.exe -m uvicorn backend.api.main:app --port 8765`):
  - Single file: `backend/api/main.py` (~250 lines). Read-only HTTP layer over the SQLite store.
  - One sqlite3 connection per process via FastAPI lifespan ctx manager; `check_same_thread=False` because uvicorn runs sync handlers in a threadpool. Pipeline workers stay the sole writers.
  - CORS wide-open (`allow_origins=["*"]`) for the upcoming Vite dev server on :5173.
  - Endpoints (all return JSON, all parametrised SQL):
    - `GET /healthz`
    - `GET /stats` — totals + breakdowns (category/intent/IOC type/source/top techniques) in one round-trip.
    - `GET /posts` — paginated list, filters: `category`, `intent`, `technique` (T-code, JOINs `post_techniques`), `q` (LIKE on body+title), `limit` (1–500), `offset`.
    - `GET /posts/{id}` — full join: post + analysis (with `targets_json`/`techniques_json` deserialised) + iocs + entities + techniques (LEFT JOIN to `mitre_techniques` so `llm_unverified` rows survive). Order: `llm_verified` first, then `semantic`, then `llm_unverified`.
    - `GET /techniques` — corpus browser, filters `q` and `only_seen`, includes `post_count` correlated subquery.
    - `GET /techniques/{T-code}` — corpus row + posts mapping to it.
    - `GET /iocs`, `GET /entities` — aggregated by `(type, value)` / `(label, text)` with occurrence count + post-id list.
  - Auto-generated docs at `/docs` (Swagger) and `/redoc`.
  - **Verified 2026-04-30:** all endpoints exercised against live DB. `/stats` shows: 235 posts, 235 LLM-analysed, 235 MITRE-matched, 176 IOCs, 239 entities, 697-technique corpus, 292 post_techniques (232 verified + 45 unverified + 15 semantic). 404 path verified on `/posts/99999`. Server stops cleanly via Ctrl-C / TaskStop.
  - Deps added to venv: `fastapi==0.115.0`, `uvicorn[standard]==0.30.6` (pulls starlette, httptools, websockets, watchfiles, python-dotenv).

- **Stage 6.5 investigations layer** (same uvicorn process as Stage 6):
  - Schema additions: `investigations` table (`id`, `name`, `description`, `filters_json`, `lens`, `summary`, `summary_model`, `summary_post_ids`, `created_at`, `updated_at`, `last_run_at`) + `idx_investigations_created`. Migration applied via `executescript` against the live DB.
  - `backend/llm/lenses.py` — 4 lenses: `threat_intel`, `ransomware`, `personal_identity`, `corporate_espionage`. System prompts inspired by Robin's `PRESET_PROMPTS` but rewritten to reference SentinelX's structured fields (IOCs/entities/MITRE techniques fed into the prompt as authoritative context, so the LLM doesn't re-derive them from text).
  - `backend/api/investigations.py` — service module: filter→SQL translator (filters: `category`, `intent`, `technique`, `q`, `ioc_type`, `since`, `until`, `post_ids`; AND-combined), filter evaluation, post-pack helper that renders one post + its enrichment as a compact LLM block, `run_lens_summary` which re-evaluates the filter at rerun time (so investigations are *live views*, not snapshots), caps inputs at `MAX_POSTS_PER_RERUN=20` and `MAX_BODY_CHARS=1200`, calls Mistral via the existing `OllamaClient`.
  - New endpoints: `GET /lenses`, `GET/POST/PATCH/DELETE /investigations[/id]`, `POST /investigations/{id}/rerun`, `GET /healthz/full`. The full health probe checks DB, Tor SOCKS5 (TCP probe only — does not open a circuit, to avoid dirtying the scraper's port pool), Ollama `/api/tags`, and pipeline cursor backlog at each of extraction/llm/mitre.
  - **Verified 2026-04-30:** `/lenses` → 4 items. Created investigation with filter `{intent:'sale', q:'credential'}` + lens `ransomware` → `matched_total=6` (post ids 210, 162, 148, 123, 100, 30 — VPN-cred sales). `POST /investigations/1/rerun` ran in 36.78s on Mistral, returned a 1744-char ransomware-lens report with `[#post_id]` citations, IOCs correctly attributed back to source posts (e.g. `okta-sso.help` cited across all 6), and a MITRE chain grounded in techniques actually mapped to the post set. `last_run_at` / `summary_model='mistral'` / `summary_post_ids` persisted. Negative paths: invalid lens → 400; DELETE → 204; subsequent GET → 404. `/healthz/full` showed db/ollama/pipeline up, tor down (Docker stack not running — expected).
  - Filter SQL gotcha caught during build: original draft used two `args.insert(0, ...)` calls for `technique` and `ioc_type` JOIN bindings, which silently swapped them when both filters were set. Fix: maintain `join_args` and `where_args` as separate lists, return `join_args + where_args` so `?` placeholders stay aligned with their JOIN/WHERE order.

### What is NOT yet done

- All seven LEARN docs (Stages 1–6 + 6.5) shipped. If voice tweaks come up, apply uniformly.
- No git commit has been made for Stage 2 / 3 / 4 / 5 / 6 / 6.5 / async-refactor / LEARN-rewrites yet beyond the existing `82470de stage 3` commit. User commits explicitly (CLAUDE.md §8).
- Stage 7 — React + Vite + Tailwind frontend. Will consume the Stage-6 + 6.5 API and render the dashboard (post list, post detail, technique browser, stats panel, **saved-investigations sidebar**, **lens selector**, **health card**, attack-graph placeholder). Design brief in `STAGE_07_DESIGN.md` (dark Avinyr-meets-Dark-Netflix aesthetic, auto-building timeline). Do NOT start without explicit user go-ahead.

### Performance ceiling for Stage 4 (so the next session doesn't re-attempt async)

The async refactor (committed in `backend/llm/{client.py,chain.py,run.py}` 2026-04-29) only buys 1.08x on the user's 4060. **Do not re-attempt async-side gains** — the GPU is the bottleneck. Real Stage-4 speedups now require either:
- Swapping to a smaller model (`phi3:mini`, `qwen2.5:3b`) — `--model` flag already supports this; ~2-3x faster, slight quality drop. Worth offering as a `--fast` mode for live demos.
- A bigger GPU. Not a software lever.
- Streaming the summary prompt and showing partial output in the UI — *might* be worth it for Stage 7's UX, not for batch throughput.

### Demo plan (for context, not built yet)

The user wants the demo to feel real — *not faked, not pre-baked theatre*. Strategy: run all stages in `--watch` mode in separate terminals. User posts a new thread on the live `.onion` forum during the demo; within ~60-90s (after concurrency refactor: ~30s) it appears fully enriched in the Stage-7 dashboard. The pre-existing 235 analysed rows serve as the "historical archive" so the dashboard isn't empty. Every output is genuinely produced by the pipeline at demo time — no canned responses.

---

## 4. Handoff protocol (READ AND FOLLOW EVERY SESSION)

This file and `PROGRESS.md` are how Claude sessions hand off to each other. The user has explicitly asked for this to be self-perpetuating, so:

**Before ending any session in which you:**
- changed code,
- completed a stage or sub-step,
- hit a blocker that the next session needs to know about,
- discovered a non-obvious gotcha (env quirk, build flag, ownership trap, etc.),
- changed the project's tech-stack or direction,

**you MUST:**
1. Update **§3 (Project status)** of this file — at minimum the date, the stage table row, and the "currently running / verified working" + "NOT yet done" lists.
2. Append a dated entry to `PROGRESS.md` describing what you did, what works, what's left, and any gotchas.
3. If you discovered a recurring gotcha or convention, add it to **§7 (Gotchas)** below.
4. Mark TodoWrite items completed as you finish them — don't batch.

If you skip this, the next Claude flies blind and the user has to re-explain the project. That's the failure mode this protocol exists to prevent.

---

## 5. Stage exit checklist

A stage is **not** considered complete until all of these are true:

- [ ] Code for the stage works end-to-end (verified by running it, not just by it compiling).
- [ ] `STAGE_XX_LEARN.md` is written at the repo root and covers what / how / why / tech-stack / industry-use exhaustively.
- [ ] `CLAUDE.md` §3 status table updated; "currently running" section reflects the new state.
- [ ] `PROGRESS.md` has a new dated entry for the stage.
- [ ] A git commit captures the stage's code + docs together (commit message format: `stage N: <one-line summary>`). **Do not commit unless the user explicitly asks.**

---

## 6. Repo layout (current)

```
SentinalX/
├── CLAUDE.md                ← this file
├── PROGRESS.md              ← stage-by-stage log
├── TECHNICAL_PRIMER.md      ← original design doc (immutable reference)
├── README.md                ← user-facing setup instructions
├── STAGE_01_LEARN.md        ← (pending)
│
├── docker-compose.yml       ← stage 1: forum + tor stack
│
├── onion_service/           ← stage 1: synthetic darknet forum
│   ├── app.py               ← Flask app w/ HTML routes + JSON API
│   ├── schema.sql           ← threads + posts tables
│   ├── seed_data.py         ← generates realistic threads/posts w/ IOCs
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── templates/           ← base, index, category, thread (Jinja2)
│   └── static/style.css     ← darknet aesthetic
│
├── tor_config/              ← stage 1: tor daemon container
│   ├── torrc                ← SOCKS5 + hidden service config
│   ├── Dockerfile
│   ├── entrypoint.sh        ← fixes bind-mount ownership, drops to debian-tor
│   └── hidden_service/      ← (gitignored) onion keypair + hostname
│
├── backend/                 ← scaffolded dirs for stages 2-6 (empty)
│   ├── api/  db/  pipeline/  scraper/
├── data/mitre/              ← (empty) MITRE JSON dump goes here in stage 5
└── frontend/                ← (empty) stage 7
```

---

## 7. Gotchas (append to this list as you discover more)

1. **Tor + Docker bind-mount ownership.** On Docker Desktop / Windows, bind mounts come into the container as `root:root` regardless of what the image set. Tor refuses to use `HiddenServiceDir` unless it's owned by Tor's runtime user with mode 700. Fix lives in `tor_config/entrypoint.sh`: container starts as root, `chown`s the mount, then `setpriv`s down to `debian-tor` before exec'ing tor. Don't revert this to a `USER debian-tor` directive in the Dockerfile — it'll break the bind mount again.
2. **`socks version 71 not recognized` warnings from tor.** Harmless. Some local process is probing 9050 as if it were an HTTP proxy and getting correctly rejected. Ignore unless it comes from one of our own services.
3. **OneDrive + Docker.** The repo lives under `OneDrive - BITS PILANI\Documents\SentinalX`. OneDrive can lock files mid-write; if you see weird Docker build errors, pause OneDrive sync and retry. Don't move the repo without asking.
4. **Forum DB persistence.** `forum_data` is a named Docker volume, not a bind mount, so reseeding requires `docker compose down -v` to wipe. The `.onion` keypair, in contrast, IS a bind mount (`tor_config/hidden_service/`) so the address survives `down -v`.
5. **The user is on Windows + bash + PowerShell.** Use Unix paths in scripts (forward slashes, `/dev/null`). Don't `cd` between commands; use absolute paths. PowerShell is available via the PowerShell tool when bash quirks bite.
6. **Host-side Python venv lives at `backend/.venv` (Python 3.12).** Created during Stage 2. Activate via `backend/.venv/Scripts/python.exe ...` from bash on Windows. Deps so far: `httpx[socks]==0.27.2`. Add a `requirements.txt` under `backend/` if dep list grows.
7. **httpx + SOCKS: use `socks5://`, not `socks5h://`.** httpx 0.27 raises `Unknown scheme for proxy URL` on `socks5h://`. The SOCKS5 transport in `httpx[socks]` already does proxy-side hostname resolution by default, which is what `.onion` needs. Don't "fix" the URL back to `socks5h://`.
8. **Splunk owns localhost:8000.** Splunk Web is bound to 127.0.0.1:8000 on the user's machine. `uvicorn --port 8000` will fail to bind ("an attempt was made to access a socket in a way forbidden by its access permissions") and curl will get 303 redirects to `/en-US/...`. Default the API to `--port 8765` (or any other free port) and tell the frontend to point there.

---

## 8. Conventions

- **One LEARN doc per stage**, named `STAGE_XX_LEARN.md` at repo root. Two-digit zero-padded.
- **Don't add features beyond what the current stage needs.** No premature abstractions.
- **No comments unless the *why* is non-obvious.** Code should be self-explanatory.
- **No emojis in code or commits.** User has not requested them.
- **Don't `git push` or open PRs without explicit user approval.** Local commits only when user says "commit this."

---

## 9. Quick commands

```bash
# Bring stage 1 stack up
docker compose up --build

# Tear down (keep volumes / .onion address)
docker compose down

# Tear down and wipe forum DB (re-seeds on next up)
docker compose down -v

# Read the .onion address
cat tor_config/hidden_service/hostname

# Hit the forum API from host (once we have the .onion)
# (will require curl --socks5-hostname 127.0.0.1:9050 once stage 2 starts)
```

---

**End of primer.** When you finish your work for this session, come back and update §3. The next Claude is depending on it.
