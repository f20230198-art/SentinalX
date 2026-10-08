# SENTINELX — PROJECT BRAIN (single source of truth for interview prep)

> Built from a full read of the repo (every backend file, both forums, Tor/Docker/deploy
> config, frontend core) + the real committed DB. All teaching modules (`study/M*.md`) are
> derived from THIS file. If something here disagrees with the code, the code wins — re-verify.
>
> Author: Srivathsa H Honyal (BITS Pilani). Interview: Derive — CTO (grills depth) + CHRO. 15 min.

---

## 0. ONE-PARAGRAPH PITCH (memorise)

SentinelX is an end-to-end **Cyber Threat Intelligence (CTI)** platform. Security teams burn
hours manually reading darknet forums for early warnings (stolen creds, 0-day PoCs, access
for sale). SentinelX automates it: a **scraper pulls posts over real Tor** (SOCKS5 → `.onion`
hidden services), **regex + spaCy extract IOCs and entities**, a **local Mistral-7B (Ollama)**
writes a summary / intent / target profile / candidate MITRE T-codes, a **MITRE ATT&CK
matcher** verifies those T-codes against the official corpus **and** discovers more via
**MiniLM sentence-embedding cosine search**, and a **FastAPI + React** dashboard shows a live
SSE timeline, ATT&CK heatmap, IOC pivot, investigation "lenses" with `[#id]` citations, a
case-file attack graph and a PDF export. **Zero paid APIs, runs on one laptop.** Every claim
traces back to a regex match, a spaCy span, a specific prompt, or a cosine score (provenance).

Key numbers (committed demo DB, verified): **441 posts** (darkbay 236, silkvault 188,
darkbay-intl 14, 1 pasted .onion = 3), 339 IOCs, 514 entities, 441 LLM analyses,
**697 ATT&CK techniques** (incl. sub-techniques), **580 post→technique mappings**
(464 llm_verified, 97 llm_unverified, 19 semantic), **44 mitigations / 1448 technique↔mitigation
links**, 2 investigations. Languages: 427 en, 9 ru, 5 es. Intents: sale 190, discussion 187,
other 48, recruitment 13, doxxing 3. (README still says "235-post" in places — stale.)

---

## 1. REPO MAP

```
README.md · render.yaml · docker-compose.yml
backend/
  db/        schema.sql (305 lines, 14 tables) · store.py (Store class) · sentinelx.db (committed demo DB)
  scraper/   client.py (JSON, DarkBay) · html_client.py (BeautifulSoup, SilkVault) · run.py (CLI)
  lang/      detect.py (langdetect) · translate.py (argostranslate)
  pipeline/  extract.py (IOC regex + spaCy NER) · run.py (extraction CLI + lang step)
  llm/       client.py (Ollama HTTP, sync+async) · prompts.py (4 prompts) · chain.py (orchestrator)
             lenses.py (4 cross-post lenses) · run.py (LLM CLI, async, semaphore)
  mitre/     ingest.py (STIX parse) · embed.py (MiniLM) · match.py (verify + top-k) · run.py (CLI)
  api/       main.py (FastAPI, 762 lines) · investigations.py · export.py (PDF)
  jobs/      runner.py (on-demand pipeline job in a thread)
frontend/src/
  App.tsx, main.tsx, lib/api.ts (typed fetch wrapper), hooks/ (useLivePosts SSE, useScramble, useCrtMode, useCursorHalo)
  pages/  Home · Timeline (617) · Heatmap · Investigations (496) · Scout · IocPivot · CaseGraph (510)
  components/ DetailPanel · CitationText · WatchIndicator · Shell · BootSequence · ShaderBackground · LanguageSwitcher
  i18n/ en/ru/es json
onion_service/            DarkBay forum (Flask, JSON API + HTML) + seed_data.py
onion_service_silkvault/  SilkVault forum (Flask, HTML ONLY, no API) + seed_data.py
tor_config/, tor_config_silkvault/   Tor daemon Dockerfile + torrc + entrypoint.sh
```

Run order (README): `docker compose up` → scraper → pipeline(extract) → llm → `mitre --ingest`
→ `mitre --once` → uvicorn (port 8765) → `npm run dev` (5173; vite proxies `/api` → 8765).

---

## 2. END-TO-END DATA FLOW (the thing to be able to draw on a whiteboard)

```
Flask forum (Docker) ──HiddenServicePort 80→forum:5000── Tor container (v3 onion service)
                                                              ▲ rendezvous through Tor network
Scraper (host) ── httpx + SOCKS5 127.0.0.1:9050/9051 ─────────┘
   │  raw_posts (UNIQUE source_post_id, cursor = MAX(source_created_at))
   ▼
Extraction step (pipeline/run.py)
   1. langdetect → lang, confidence;  if non-English & conf ≥ 0.85 → argostranslate → body_en
   2. IOC regex on ORIGINAL body (language-agnostic, translation mangles hashes)
   3. spaCy NER + curated MALWARE/THREAT_ACTOR on ENGLISH text
   → iocs, entities;  raw_posts.processed_at stamped
   ▼
LLM step (llm/run.py)  reads COALESCE(body_en, body) + KNOWN FACTS (IOCs/entities)
   4 independent prompts fired concurrently (asyncio.gather): summary | intent | targets | techniques
   → llm_analyses;  post_processing_state(stage='llm')
   ▼
MITRE step (mitre/run.py)
   A. verify LLM T-codes vs corpus → llm_verified / llm_unverified
   B. embed post (MiniLM, 384-d, L2-norm) · matrix @ vec = cosine · top-k=5 · threshold 0.45 → 'semantic'
   → post_techniques;  post_processing_state(stage='mitre')
   ▼
FastAPI (api/main.py) — REST + SSE + investigations + PDF + jobs + health
   ▼
React dashboard (Vite, TanStack Query, d3-force, framer-motion, three.js shader, i18next)
```

**Stage independence:** each stage has its own cursor in `post_processing_state(raw_post_id, stage)`
(PK on both). A stage selects rows with `NOT EXISTS (… stage = ?)`. So any stage can be re-run,
interrupted, or run in `--watch` mode safely = **idempotent, resumable pipeline**.

---

## 3. CONCEPT GLOSSARY (CTO may probe ANY of these)

**CTI** — Cyber Threat Intelligence: evidence-based knowledge about threats (actors, tools, TTPs, IOCs) to help defenders act.
**IOC** — Indicator of Compromise: artefact that signals compromise (IP, domain, URL, hash, CVE, BTC address, email).
**TTP** — Tactics, Techniques, Procedures: *how* an attacker operates. IOCs are brittle ("pyramid of pain": hashes trivial to change, TTPs hard).
**MITRE ATT&CK** — public knowledge base of adversary behaviour. **Tactic** = the *why* (14 enterprise columns: Recon, Resource Dev, Initial Access, Execution, Persistence, Priv-Esc, Defense Evasion, Credential Access, Discovery, Lateral Movement, Collection, C2, Exfiltration, Impact). **Technique** = the *how* (T1566 Phishing). **Sub-technique** = finer (T1566.001 Spearphishing Attachment). **Mitigation** = defensive control (M1049 Antivirus). T-code regex used: `^T\d{4}(\.\d{3})?$`.
**STIX 2.1** — JSON standard for CTI. ATT&CK ships as one ~30 MB STIX bundle (`enterprise-attack.json` from github mitre/cti). Objects we use: `attack-pattern` (technique), `course-of-action` (mitigation), `relationship` (`subtechnique-of`, `mitigates`). We skip `revoked` / `x_mitre_deprecated`. External ID found in `external_references` where `source_name == "mitre-attack"`. Kill-chain phases (`kill_chain_name == "mitre-attack"`) give tactics.
**Defanging** — writing `evil[.]com`, `hxxp://`, `1.2.3[.]4` so IOCs aren't clickable. We **refang** before regex.
**NER** — Named Entity Recognition (spaCy `en_core_web_sm`, small CNN pipeline; we disable `parser` & `lemmatizer` for speed). Keep labels PERSON/ORG/GPE/NORP/PRODUCT/EVENT/LOC. spaCy can't know "Cobalt Strike" → curated keyword lists (case-sensitive: "Conti" vs "conti").
**Tor / onion routing** — traffic wrapped in layers of encryption, relayed through ≥3 relays (guard→middle→exit); each relay knows only prev/next hop. **Hidden (onion) service v3** — server publishes a descriptor to HSDirs; client + service meet at a **rendezvous point**; address = base32 of ed25519 public key + checksum + version (56 chars, `.onion`); never leaves Tor (no exit node). **SOCKS5** — proxy protocol; client hands hostname to proxy → *proxy-side DNS* (critical: `.onion` can't be resolved locally).
**LLM (Mistral 7B via Ollama)** — 7-billion-parameter open-weights decoder-only transformer, run locally. Ollama = local server (port 11434) exposing `/api/generate`, `/api/tags`. Typically 4-bit quantised GGUF (default tag `mistral:latest`) so it fits consumer RAM/VRAM. **Temperature** 0.2 (low = more deterministic). **num_predict** = max output tokens. **format=json** = constrained JSON decoding. Context ~8k tokens → we clip body to 4000 chars.
**Prompt chaining** — splitting a task into several prompts. NOTE: in our code the 4 prompts are *independent* → run in **parallel**, not a true sequential chain (name is historical; know this).
**Hallucination** — model invents facts (e.g., nonexistent T-codes). Mitigations here: KNOWN FACTS block, "do not invent", JSON schema-by-example, **post-hoc verification against the real corpus**, store raw responses.
**Embeddings / cosine similarity** — text → vector; similar meaning → near vectors. `all-MiniLM-L6-v2`: 384-dim, 6-layer distilled model (~22M params), CPU-friendly, trained contrastively on 1B+ pairs; max seq len 256 word-pieces (longer text is truncated — real limitation). We L2-normalise ⇒ cosine = dot product ⇒ one matrix multiply scores a post against all 697 techniques.
**Semantic search** — nearest neighbours in embedding space. Brute force is fine at N=697 (matrix ≈ 697×384×4 B ≈ 1 MB); a vector DB (FAISS/pgvector) only needed at >100k.
**Top-k with `np.argpartition`** — O(N) partial selection of k best, then sort only those; vs full sort O(N log N).
**SSE (Server-Sent Events)** — one-way HTTP stream, `text/event-stream`, frames `event:/data:\n\n`, browser `EventSource` auto-reconnects. vs **WebSocket** (bi-directional, upgrade handshake, heavier) vs **polling**. We only push server→client → SSE is the right tool.
**ASGI / FastAPI / uvicorn** — async Python web stack; FastAPI uses type hints → validation + OpenAPI docs; sync `def` endpoints run in a threadpool, `async def` on the event loop.
**CORS** — browser rule blocking cross-origin reads unless server sends `Access-Control-Allow-*`. Needed because Vercel (frontend) ≠ Render (API). Default `*`; env `CORS_ORIGINS` narrows.
**SQLite** — embedded file DB. **WAL mode** — readers don't block the writer. `busy_timeout=5000` — wait on lock instead of failing. `PRAGMA foreign_keys=ON` (off by default!). `ON DELETE CASCADE`. `UNIQUE` constraints give free dedup. BLOB for embeddings.
**Idempotency** — running an operation N times = running once. Done via UNIQUE constraints + `INSERT … ON CONFLICT DO UPDATE` + per-stage cursors.
**Async + Semaphore** — `asyncio.gather` runs coroutines concurrently; `Semaphore(n)` caps concurrency. Peak in-flight Ollama calls = concurrency × 4.
**Daemon thread** — background worker that dies with the process. Used for scrape jobs.
**d3-force** — physics simulation (many-body charge repulsion, link springs, collision, centering); we run it in React and re-render SVG on each `tick`.
**TanStack Query** — server-state cache: `useQuery` (cache + staleTime 30 s + retry 1), `useQueries` (N parallel), `useMutation` (+ `invalidateQueries`), `refetchInterval` for polling.
**i18next / react-i18next** — UI translations en/ru/es; LanguageDetector order `localStorage` → `navigator`; fallback en.
**WeasyPrint** — HTML+CSS → PDF; needs GTK/Pango native libs (why Render can't do PDF).
**Vite proxy** — dev: `/api/*` → `127.0.0.1:8765` (rewrite strips `/api`) ⇒ same-origin, no CORS in dev. Prod: `VITE_API_BASE` → Render URL.

---

## 4. BACKEND — MODULE BY MODULE (what, how, why)

### 4.1 Forums (the data source) — `onion_service/`, `onion_service_silkvault/`
- Why synthetic: scraping real darknet = legal/ethical/availability issues; but the *Tor plumbing is real* (genuine v3 hidden services, real SOCKS5 circuits). Same scraper code would work on a real site.
- **DarkBay** (Flask+gunicorn, SQLite): threads/posts schema; HTML pages **and** `GET /api/posts?since=&limit=&category=` (returns `posts[]`, `now`), `POST /api/threads` (inject live demo content), `/healthz`. Timestamps = Unix epoch floats so `since` is numeric.
- **SilkVault**: *deliberately different* — "listings/messages", `/board/<slug>`, `/listing/<id>`, **no API at all**, HTML only, `<div class="vault-message" data-message-id data-epoch>` + `<time datetime>`. Purpose: prove the scraper isn't hard-coded to one site/format. `/new` HTML form lets you post a thread live in Tor Browser.
- `seed_data.py`: templated posts rich in IOCs (fake IPs, `[.]` defanged domains, BTC, sha256, CVEs, actors, malware). Seeded with `--seed 42` (reproducible). Container CMD seeds DB on first boot if absent, then `exec gunicorn -w 2`.
- Both use per-request SQLite connection in `flask.g`, closed in `teardown_appcontext`.

### 4.2 Tor + Docker — `docker-compose.yml`, `tor_config*/`
- 4 services: `forum`, `tor`, `silkvault`, `tor-sv`. Forums `expose` 5000 only inside the compose network (not published to host!). Tor containers publish SOCKS5 to **host loopback only** (`127.0.0.1:9050` DarkBay, `127.0.0.1:9051→9050` SilkVault).
- `torrc`: `SOCKSPort 0.0.0.0:9050` (so host can reach via published port), `HiddenServiceDir`, `HiddenServiceVersion 3`, `HiddenServicePort 80 forum:5000` (onion:80 → container forum:5000), `ClientOnly 1`, logs to stdout.
- Key pair lives in `hidden_service/` **bind-mounted** → `.onion` address stable across rebuilds. Hostname file read by `read_onion_hostname()`.
- `entrypoint.sh`: Tor refuses to start unless HiddenServiceDir is owned by the Tor user with mode 700; Docker Desktop bind mounts arrive root-owned → script `chown`/`chmod 700` then `setpriv` drops to `debian-tor` (least privilege) and `exec`s tor.
- `depends_on: condition: service_healthy` + Python-based healthchecks (`/healthz`) so Tor starts only after Flask is ready.

### 4.3 Scraper — `backend/scraper/`
- `client.py` `ForumClient`: `httpx.Client(proxy="socks5://127.0.0.1:9050")`, timeouts connect 30/read 60 (Tor is slow), `follow_redirects=False`. **Gotcha documented in code:** use `socks5://` not `socks5h://` — httpx rejects `socks5h`, and its SOCKS5 transport already does proxy-side DNS by default.
- `html_client.py` `HtmlForumClient`: crawl index → collect `/listing/N` links (`a.listing-card`, fallback regex) → fetch each → BeautifulSoup parse messages (data-* attrs first, then `<time>` fallback; drop messages w/o id/epoch/body) → post dicts in the same shape as the JSON client. Bounded: `max_listings=500`. One bad page doesn't kill the crawl (errors collected). Client-side `since` filter (forum has no server-side since). Sorted by time.
- **ID-collision fix:** `source_post_id` is globally UNIQUE but each forum numbers from 1 ⇒ HTML scraper adds `offset=(sum(bytes of source label) % 1000 + 1) × 1e9`. Deterministic (Python `hash()` is salted per process), keeps DarkBay's low-id block free. *Weakness:* byte-sum isn't collision-resistant (anagram labels collide) — a proper fix is a composite key `(source, source_post_id)`.
- `run.py` CLI: `--once | --watch --interval | --reset-cursor`, `--html --url --proxy --source`. `store.run()` context manager writes a `scraper_runs` audit row (cursor_before/after, fetched/inserted/duplicates, error).
- **Incremental cursor = `MAX(source_created_at)`** from the data itself (no separate state row ⇒ can't drift). Query is strictly `created_at > since`. *Caveats:* late-arriving posts with older timestamps are missed; mitigated by UNIQUE dedup on re-crawl. *Known inconsistency:* CLI `poll_html_once` uses the **global** cursor (so a SilkVault crawl is held back by DarkBay's newer posts), whereas `jobs/runner.py` uses the **per-source** cursor `get_cursor(source=…)`. Be ready to admit this.

### 4.4 Store / schema — `backend/db/`
Tables (14): `raw_posts`, `scraper_runs`, `iocs`, `entities`, `extraction_runs`, `llm_analyses`, `post_processing_state`, `llm_runs`, `mitre_techniques`, `post_techniques`, `mitre_runs`, `mitre_mitigations`, `technique_mitigations`, `investigations`, `pipeline_jobs`.
- `raw_posts`: id, source_post_id UNIQUE, source_thread_id, thread_title, category, author, body, source_created_at, fetched_at, **source** (forum label), **lang, lang_confidence, body_en**, **processed_at** (added by guarded ALTER).
- `iocs UNIQUE(raw_post_id, ioc_type, value)`, `entities UNIQUE(raw_post_id, label, text)`; spans stored.
- `llm_analyses` one row per post (UNIQUE raw_post_id): summary, intent, targets_json, techniques_json, model, raw_responses (for reproducibility/debug).
- `post_techniques UNIQUE(raw_post_id, technique_id, source)` — source ∈ {llm_verified, llm_unverified, semantic}; score (cosine) & evidence.
- `mitre_techniques.embedding BLOB` = float32×384 (+ `embedding_model` recorded so a model change is detectable).
- `investigations`: filters_json (a *saved query*, re-evaluated on each read = live view not snapshot), lens name (lenses live in code → prompt changes need no migration), summary, summary_post_ids.
- `pipeline_jobs`: observability row for on-demand jobs (status queued|running|done|error, stage, counts, message).
- `Store._init_schema`: `executescript(schema.sql)` (all `IF NOT EXISTS`) + **guarded `ALTER TABLE ADD COLUMN`** after `PRAGMA table_info` (SQLite lacks `ADD COLUMN IF NOT EXISTS`) = lightweight idempotent migrations. API `lifespan` runs `Store(DB_PATH).close()` once to upgrade an old DB.
- `insert_posts`: tries INSERT, catches `IntegrityError` → counts duplicates (cheap dedup).

### 4.5 Language — `backend/lang/`
- `detect.py`: `langdetect` (Python port of Google's language-detection; naive-Bayes over character n-grams). **Seeded** (`DetectorFactory.seed=0`) because it's randomised ⇒ deterministic re-runs. `< 20 chars` → `unknown`. `zh-cn/zh-tw → zh` (argos wants bare ISO-639-1). `needs_translation` only if lang ∉ {en, unknown} **and confidence ≥ 0.85** — because short IOC-heavy jargon posts get wrong guesses at 0.5-0.6 ("APT41 affiliated?" → Danish) while real ru/es detect at ~0.99; low-confidence non-English is treated as English (normalised to `'en'`).
- `translate.py`: `argostranslate` = offline neural MT (OPUS-MT / CTranslate2). Per-direction packages (`ru→en`), ~100 MB, auto-installed on first use, memoised (`_install_attempted/_install_ok`). Chunks ≤1500 chars on paragraph boundaries. **Never raises**: failure → `ok=False`, original text returned ⇒ graceful degradation.
- **Why translate before everything:** spaCy small-English NER is garbage on Cyrillic/CJK; keyword lists are English; Mistral reads Russian but summarises it worse. **Why IOCs from the ORIGINAL body:** MT mangles long hash/BTC strings; IOCs are language-agnostic.
- `pipeline/run.py::_resolve_language`: reuse previous result if `row["lang"]` set; else detect → maybe translate → returns (lang, conf, body_en). `reset` clears lang fields to force re-run.

### 4.6 Extraction — `backend/pipeline/extract.py`
IOC types: `ipv4, ipv6, cve, md5, sha1, sha256, btc, url, domain, email`.
- `refang()` first. Regexes: IPv4 `\b(?:\d{1,3}\.){3}\d{1,3}\b` **then validate each octet ≤255**; CVE `\bCVE-\d{4}-\d{4,7}\b` (upper-cased); hashes by length (32/40/64 hex) processed **longest-first** with span-overlap check so a sha256 isn't also read as md5/sha1; BTC = bech32 `bc1…` or legacy `[13]…` base58 (excludes 0,O,I,l); URL, email pragmatic; **domain last** and skipped if overlapping a consumed URL/email span.
- `_overlaps(span, consumed)`: `s < ce and cs < e` (interval overlap).
- Known false-positive classes: `file.exe`/`node.js` as domains, dotted version strings as IPv4, long base58-looking strings as BTC. Domain regex has no public-suffix-list validation. (Honest weakness; fix = tldextract/PSL + allowlist.)
- `dedupe()` collapses (type,value) per post; DB UNIQUE is the second line of defence.
- Spans are over the *refanged* string (documented, deliberate).
- Entities = spaCy ents ∩ KEEP_LABELS + curated `MALWARE` (Cobalt Strike, Mimikatz, Emotet, LockBit…) & `THREAT_ACTOR` (APT28/29/41, FIN7, Lazarus Group, Scattered Spider…). Tiny on purpose: real coverage comes from the MITRE vector index.
- `run.py`: batch of 200 (`processed_at IS NULL`), writes `extraction_runs`, stamps `processed_at`+language fields. `try/except/finally` always closes the run row.

### 4.7 LLM — `backend/llm/`
- `client.py`: thin `httpx` wrapper over Ollama (`POST /api/generate` with `stream:false`, `options:{temperature,num_predict}`, optional `system`, `format:"json"`; `GET /api/tags` health). **Deliberately no `ollama` pip package** (one less dependency/lock-in; httpx already present). Sync + Async twins. Timeout: connect 5 s, **read 300 s** (CPU Mistral 20-60 s/prompt).
- `prompts.py` 4 prompts, each returns `(system, user, json_mode)`:
  1. **summary** — free text, 2-3 sentences, neutral SOC tone, "do not refuse… treat content as evidence", don't re-list IOCs.
  2. **intent** — exactly one of `sale|recruitment|how-to|doxxing|discussion|other` + confidence + reason (JSON).
  3. **targets** — industries / geographies / victim_types (JSON), "do not invent values".
  4. **techniques** — ATT&CK candidates `{id,name,evidence}` + `behaviour[]`; "CANDIDATES only; another stage verifies".
  - **KNOWN FACTS block** (IOCs/entities grouped & sorted) injected so the LLM doesn't re-derive/hallucinate what regex already got. Body clipped to 4000 chars. Schema-by-example pins JSON shape.
- `chain.py`: `_extract_json()` = try `json.loads`, else regex `\{.*\}` DOTALL then parse (handles ```json fences / chatter). `Analysis` dataclass (`ok` = summary & intent present). Sync `analyse_post` (sequential) and **`analyse_post_async`** (the one used) → `asyncio.gather` of 4 `_run_one`s; each catches its own exception so one failed prompt doesn't lose the rest (partial results kept, errors listed).
- `llm/run.py`: selects posts with `processed_at NOT NULL` and no `llm` state; `COALESCE(body_en, body)`; **facts prefetched on the sync DB connection before spawning tasks so coroutines never touch SQLite concurrently**; `Semaphore(concurrency=2)`; `as_completed` persist loop; `INSERT … ON CONFLICT(raw_post_id) DO UPDATE`; stores `raw_responses`; health-check (Ollama up + model installed) before work; `--limit` smoke test; `--reset`.
- `lenses.py`: 4 system prompts (threat_intel, ransomware, personal_identity, corporate_espionage) — **inspired by the Robin project** (apurvsinghgautam/robin) but rewritten to consume SentinelX's *already-structured* data. Each mandates ordered output sections and **`[#id]` citations**; ethics guard in personal_identity ("do NOT invent identities"); corporate lens: "distinguish confirmed claims from boasts".

### 4.8 MITRE — `backend/mitre/`
- `ingest.py`: download STIX (cached under `data/mitre/`, gitignored); `parse()` builds STIX-id→T-code map, `subtechnique-of` relationships → `parent_id`; drops revoked/deprecated; tactics from kill-chain phases. `parse_mitigations()` reads `course-of-action` + `mitigates` relationships (source=CoA, target=attack-pattern), dedupes, FK-safe. Result: 697 techniques, 44 mitigations, 1448 links.
- `embed.py`: lazy-loaded (`lru_cache`) `SentenceTransformer("all-MiniLM-L6-v2")` (imports torch, 3-5 s); `normalize_embeddings=True`; float32; `to_blob/from_blob/stack_blobs`.
- Ingest text per technique = `"{name}. {description}"`; upsert on conflict.
- `match.py`:
  - `parse_llm_candidates` tolerant of `{techniques:[…]}` / list / strings.
  - `normalise_tcode` strict regex.
  - `verify_llm`: **verified** iff T-code exists in corpus, else **unverified** (hallucinated/invalid/deprecated). ⚠ "verified" = *exists*, **not** "correct for this post".
  - `semantic_topk(post_vec, matrix, ids, topk=5, threshold=0.45, exclude)`: `scores = matrix @ post_vec` → `argpartition` for top (k+|exclude|+5) → sort → skip excluded (already LLM-verified, no double count) → stop below threshold.
- `run.py`: `--ingest` (also mitigations) / `--ingest-mitigations` / `--once` / `--watch` / `--reset` / `--reset-corpus`; batch 25; posts embedded **in one batch** (faster); `include_llmless=True` path = pure-semantic for fast-mode jobs that skipped the LLM.
- **Hybrid rationale:** LLM = high recall + natural-language evidence but hallucinates; embeddings = grounded in the real corpus, deterministic, auditable (score) but coarse; verification step turns LLM output into grounded output. Mitigations are pure **lookup** (no LLM) ⇒ trustworthy defensive advice.

### 4.9 API — `backend/api/main.py` (FastAPI 0.115, v0.6.5)
- Config via env: `SENTINELX_DB_PATH`, `CORS_ORIGINS`. One shared read connection in `app.state.conn` (`check_same_thread=False`, WAL, busy_timeout). Writers (pipeline/job threads) use their own connections.
- Endpoints: `GET /healthz`; `/healthz/full` (db latency, **TCP probe to Tor :9050** — doesn't build a circuit, Ollama `/api/tags`, pipeline backlog counts pending_extraction/llm/mitre); `/stats` (aggregates incl. by_language, top_techniques); `/posts` (filters category/intent/technique/q + pagination; `substr(COALESCE(body_en, body),1,280)` preview; `LIKE` over body/body_en/title); `/posts/{id}` (post+analysis+iocs+entities+techniques+**mitigations**); `/techniques`, `/techniques/{id}`; `/iocs`, `/entities` (GROUP BY + `GROUP_CONCAT(raw_post_id)` for pivoting); `/lenses`; investigations CRUD + `/rerun` + `/export`; `POST /scrape-jobs` (202, thread), `GET /scrape-jobs[/id]`; **`GET /events` (SSE)**.
- `_mitigations_for_techniques`: dynamic `IN (?,?,…)` placeholders (parameterised ⇒ no SQL injection), group by mitigation, `addresses` + `coverage`, sort coverage desc.
- **SSE** `/events?since_id=`: async generator; sends `hello{latest_id}`, then loops: `if await request.is_disconnected(): break`; poll `id > last LIMIT 50` every 2 s, emit `post` frames (snapshot with techniques + ≤8 IOCs), `ping` every 15 s keepalive; headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`. *Why polling the DB rather than pub/sub:* scraper is a separate process/sole writer, scale is tiny, EventSource reconnect covers hiccups. *Weakness:* synchronous sqlite calls inside an async generator block the event loop; one poll loop per client; no `Last-Event-ID`.
- `POST /scrape-jobs`: validates `.onion`, normalises host, creates row, starts **daemon thread** running `runner.run_job` which opens its own Store.
- `investigations.py`: filter schema {category, intent, technique, q, ioc_type, since, until, post_ids} → `_build_where` builds parameterised SQL (join args kept in order before where args); **live-view** semantics; `run_lens_summary` samples ≤20 posts × ≤1200 chars (context budget), packs `=== POST [#id] ===` blocks with INTENT/IOCS/ENTITIES/MITRE, calls Ollama with lens system prompt (temp 0.2, 1500 tokens), overwrites summary (no history — documented trade-off). `aggregate_mitigations` = "do this first" list ranked by posts covered.
  - ⚠ small inconsistencies: filter `q` and lens packing use `body` not `body_en` (non-English posts fed raw to the lens LLM).
- `export.py`: builds HTML by hand (all values `html.escape`d), `[#id]` → `<sup>` footnote anchors, MITRE coverage table with CSS bars, mitigations table, per-post appendix, then `weasyprint.HTML(string=…).write_pdf()`. Lazy import so missing GTK doesn't break API; `OSError` → clear "GTK missing" 500.

### 4.10 Jobs — `backend/jobs/runner.py` (the "Scout" feature)
Paste any `.onion` → daemon thread → stages: **scrape (HTML, per-source cursor) → extract → LLM (`_try_llm`: health-check Ollama; batches of 10 × concurrency 4; updates `pipeline_jobs` between batches) → MITRE (`include_llmless=skip_llm`) → done**. Updates job row after each stage (`status/stage/message/counts`). Frontend polls every 2 s. **Graceful degradation:** Ollama down ⇒ `llm_skipped=1`, job continues with extraction + semantic MITRE. **Fast mode** `skip_llm`. Drains pending work even when 0 new posts. Never raises (top-level guard → status=error). Runs stage functions **in-process** (same code as the CLIs). *Weakness:* thread dies with server; a restart leaves a job stuck `running`; for scale use a queue (Celery/RQ/Arq).

---

## 5. FRONTEND (React 18 + Vite 6 + TS + Tailwind v4)
- `main.tsx`: `QueryClientProvider` (staleTime 30 s, no refetch on focus, retry 1) + `BrowserRouter` + side-effect `import "./i18n"`.
- `App.tsx`: `BootSequence` (5-phase cinematic intro, once per session via sessionStorage) + `ShaderBackground` (three.js full-screen quad, fBM domain-warp fragment shader, DPR cap 1.5, pauses on hidden tab) + `Header` + routes: `/` Home, `/posts` Timeline, `/techniques` Heatmap, `/investigations`, `/scout`, `/iocs/:value`, `/investigations/:id/graph`. (Decorative = low interview priority; know it exists and why.)
- `lib/api.ts`: typed `get/post` wrappers, `API_BASE = VITE_API_BASE ?? "/api"`, TS interfaces mirror FastAPI shapes; `URLSearchParams` drops null/empty params.
- **Timeline**: vertical "timecord"; bootstraps by opening EventSource `?since_id=0` (replays all history), after 2.5 s silence → `historyDone` → `useLivePosts(maxId)` opens a second stream for new posts; dedupe by id; arrival animation 4.5 s; filter chips (sale/discussion/doxxing/recruitment/with_cve/with_btc) dim non-matches. *(Detail: first stream is never closed so live posts arrive on both; merge Map dedupes.)*
- **Heatmap**: Navigator-style matrix, 14 tactics in 2 rows, cell shade = log-scaled `post_count` (`log(1+c)/log(1+max)`), click → technique posts → DetailPanel. Techniques with multiple tactics appear in multiple columns.
- **Investigations**: list + detail, lens summary rendered via `CitationText` (regex `\[#(\d+)\]` → clickable chips → DetailPanel), rerun mutation, PDF link = plain `<a href>` to `/export`, mitigations list.
- **CaseGraph**: for an investigation, up to 25 posts → `useQueries` fetch each `PostDetail` → build nodes: post (circle), IOC (diamond, colour by type), MITRE (square); **shared IOC/technique = single node (Map keyed `ioc:type::value`, `mitre:Txxxx`)** ⇒ clusters emerge. d3-force: charge (-240 posts / -120 others), link distance 70/90, collide radius, center; `alphaDecay 0.035`; `tick → setState` re-render SVG; hover highlights neighbours via precomputed adjacency `Map<string,Set<string>>`; cleanup `sim.stop()`.
- **IocPivot**: central IOC + satellite posts + "co-occurring IOCs" pivot chips (graph traversal by clicking).
- **Scout**: URL input + fast-mode checkbox → `useMutation` → poll job via `refetchInterval` (2 s until done/error) → stage checklist.
- **WatchIndicator**: header pill polling `/healthz/full` every 12 s.
- **i18n**: UI chrome only (nav, labels); data (post bodies, technique names, IOCs) not translated; persisted in localStorage `sentinelx.lang`.
- Hosting: Vercel (`vercel.json` SPA rewrite `/(.*)→/index.html` so client routes survive refresh).

---

## 6. DEPLOYMENT & TRADE-OFFS
- **Frontend → Vercel**; **API+SQLite → Render free** (`render.yaml`: `uvicorn backend.api.main:app --host 0.0.0.0 --port $PORT`, `SENTINELX_DB_PATH=backend/db/sentinelx.db`, health `/healthz`, ~30-60 s cold start). The enriched demo DB is **committed** (force-added past `*.db` in .gitignore) so the hosted API has data on day one.
- **Local only:** Ollama/Mistral (needs GPU/RAM; no free GPU hosting), Tor + forums (free PaaS bans Tor).
- **PDF export fails on Render** (no GTK in Python runtime) → would need a Docker service.
- Render disk is ephemeral ⇒ any writes lost on redeploy; hosted site is effectively a read-only demo.
- Cross-origin SSE works only if CORS is configured; free-tier sleep delays first request.

---

## 7. HONEST WEAKNESSES / LIKELY GRILL TARGETS (own them before they're found)
1. **"Chain" isn't a chain** — 4 independent parallel prompts. Fine design (no data dependency), name is misleading.
2. **No ground-truth evaluation** — data is synthetic and templated; no precision/recall for IOC extraction, intent or technique mapping. Only proxy metric: verified vs unverified (464 / 97 ≈ 17 % of LLM-proposed T-codes are invalid). Next step: labelled eval set (~100 posts), compute P/R/F1, compare LLM-only vs semantic-only vs hybrid.
3. **"Verified" ≠ "correct"** — only checks the T-code exists in the corpus.
4. **Semantic matching is coarse** — MiniLM 256-token limit truncates long posts; embedding whole post vs technique text; threshold 0.45 picked empirically (only 19 semantic hits). Better: chunk posts, reranker/cross-encoder, fine-tune on ATT&CK procedure examples, or use the technique *procedure examples* not just description.
5. **Regex IOC false positives** (domains/versions/BTC lookalikes); no PSL validation; no enrichment (VT/whois).
6. **Prompt injection** — scraped text goes straight into prompts; a hostile post could try to steer output. Mitigations present: system-role framing, JSON mode, downstream verification, human-readable evidence; absent: input sanitisation / separate untrusted-data delimiters / output schema validation (e.g. pydantic).
7. **Scale** — SQLite + single process + polling SSE + in-process thread jobs + brute-force vectors. Fine for demo/one analyst; would move to Postgres(+pgvector), a queue (Celery/RQ), pub/sub (Redis/NOTIFY) and horizontal API.
8. **Cursor design** — `MAX(created_at)` misses late/out-of-order posts and has the global-vs-per-source inconsistency in the CLI HTML path.
9. **ID offset hash** collision-prone (byte-sum). Use composite unique `(source, source_post_id)`.
10. **No auth / rate-limit / RBAC**, CORS `*` default, API trust model = localhost demo.
11. **Event-loop blocking** — sync sqlite calls inside the async SSE generator; shared connection across threadpool threads.
12. **Job robustness** — daemon thread dies with process; stuck `running` rows on restart; no retries/backoff.
13. **Ethics/legal** — synthetic forums by design; real-world use needs legal review, OPSEC (don't browse from corporate IP; use isolated VM), no interaction with actors, data handling of PII (personal_identity lens avoids amplifying).
14. **Translation errors** propagate into NER/LLM; low-confidence gate trades recall for safety.
15. Lens rerun overwrites history (documented), samples only 20 posts.

---

### Status after the Oct-2026 improvement pass (see §9)
- #2 evaluation → **DONE** (harness + numbers; technique mapping turned out weak — own it).
- #4 semantic truncation → **chunking + max-pooling DONE**; matcher still weak (see eval).
- #5 IOC FPs → **PSL filter DONE** (domain precision 0.68→0.82); `.md`/`.py` remain (real ccTLDs!).
- #6 prompt injection → **fencing + schema validation DONE** (still not "solved" — layered defence).
- #8 cursor inconsistency → **FIXED** (per-source everywhere).
- #9 ID collision → **FIXED** (composite `UNIQUE(source, source_post_id)` + migration).
- #10 auth/rate-limit → **optional API key + per-IP rate limit DONE**.
- #11 SSE event-loop blocking → **FIXED** (threadpool) + `Last-Event-ID` resume.
- Still open: scale (#7), job robustness (#12), lens history (#15).

---

## 8. QUESTIONS THE CTO IS MOST LIKELY TO ASK (answers live in M11 drill; headlines here)
- Walk me through the architecture end-to-end. Why this stack?
- How does a request reach a `.onion` from your laptop? Why `socks5` not `socks5h`? What is a rendezvous point?
- Why SQLite? What breaks first at 100× scale? Why WAL?
- How is the pipeline idempotent / how do you resume after a crash?
- How do you stop the LLM hallucinating? How do you evaluate it? What's the model, quantisation, context window, temp?
- Why embeddings AND an LLM? Explain cosine similarity, why normalise, what is MiniLM, why 0.45?
- Why SSE not WebSockets? How does reconnect work? What's wrong with your polling?
- Explain the IOC regexes, defanging, overlap handling, false positives.
- Why translate before NER and extract IOCs from the original?
- How would you productionise it? Security of the system itself (prompt injection, SSRF — note: `POST /scrape-jobs` accepts arbitrary `.onion` host → SSRF-ish risk bounded by `.onion` validation)?
- What was the hardest bug? (Candidates from code comments: httpx rejecting `socks5h`; Tor refusing bind-mount perms on Docker Desktop; langdetect non-determinism & false non-English on jargon; forum ID collisions across sources; SQLite lock between API reads and job writes → WAL + busy_timeout; StrictMode double-mount re-running boot animation.)
- What did *you* build vs use? (Be honest: libraries do NER/MT/embeddings/LLM; your work = system design, extraction rules, orchestration, verification logic, idempotent stages, API, UI.)

---

## 9. IMPROVEMENT PASS (Oct 2026) — what changed, where, and how to talk about it

| # | Change | Files | One-line interview framing |
|---|---|---|---|
| 1 | **Evaluation harness** | `backend/eval/{run.py,ioc_cases.py,gold_posts.json,EVAL_REPORT.md}` | "I stopped claiming accuracy and measured it." |
| 2 | **Composite post key** `UNIQUE(source, source_post_id)` + in-place SQLite table-rebuild migration (ids preserved, FKs intact, idempotent) | `db/schema.sql`, `db/store.py::_migrate_composite_post_key` | "Post identity is (forum, id) — the old byte-sum offset hack was collision-prone." |
| 3 | **Per-source cursor** in CLI scrapers (was global → SilkVault crawls silently skipped posts older than DarkBay's newest) | `scraper/run.py` | "Found a silent data-loss bug in my own code; there's a test for it now." |
| 4 | **38 pytest tests + GitHub Actions CI** (backend tests + frontend typecheck) | `tests/`, `pytest.ini`, `.github/workflows/ci.yml`, `requirements-dev.txt` | "No Ollama/Tor needed to test — units are isolated." |
| 5 | **Public Suffix List domain filter** (tldextract, offline snapshot) | `pipeline/extract.py::_is_real_domain` | "node.js / loader.exe no longer count as domains." |
| 6 | **URL trailing-punctuation fix** (found while building eval cases) | `pipeline/extract.py` | "Evaluation found a bug the demo never showed." |
| 7 | **Chunked embeddings + max-pooling** (MiniLM 256-token limit) | `mitre/match.py::chunk_text`, `semantic_topk`, `mitre/run.py` | "A technique described late in a long post was invisible before." |
| 8 | **Pydantic schema validation** of all JSON LLM outputs (closed intent vocabulary, clamped confidence, T-code shape, bounded lengths, malformed items dropped not fatal) | `llm/schemas.py`, `llm/chain.py::_apply_json` | "`format=json` guarantees syntax, not semantics." |
| 9 | **Prompt-injection fencing**: untrusted markers, marker look-alikes neutralised, system-prompt rule; also on the lens prompt | `llm/prompts.py`, `api/investigations.py` | "Layered: fence → validate → verify against corpus. Bounded, not solved." |
| 10 | **SSE**: `id:` per frame + `Last-Event-ID` resume; sqlite calls via `run_in_threadpool` | `api/main.py::events_stream` | "Reconnects resume exactly; one slow query no longer stalls every request." |
| 11 | **Optional API key** (`SENTINELX_API_KEY`, `X-API-Key` on writes) + **sliding-window per-IP rate limit** on scrape-jobs / reruns (429 + Retry-After) | `api/main.py::protect_writes`, `frontend/src/lib/api.ts` | "Reads open, writes gated; in-memory limiter → Redis for multi-worker." |
| 12 | **Dockerfile.api** with Pango/HarfBuzz so PDF export works hosted; slim `requirements-api.txt` (no torch) | `Dockerfile.api`, `backend/requirements-api.txt` | "Separated serving deps from pipeline deps." (NOT build-tested yet — Docker was off.) |
| 13 | Lens packing + investigation `q` filter now use `body_en` (consistency with per-post chain) | `api/investigations.py` | — |

### Evaluation results (draft gold labels — REVIEW them before quoting)
- **IOC extraction** (80 constructed cases): overall **P 0.93 / R 0.95 / F1 0.94**. btc/cve/email/md5/sha256/url perfect. Domain P 0.82 (was **0.68** before PSL filter). Remaining FPs: `readme.md`, `setup.py` — `.md` (Moldova) and `.py` (Paraguay) are *real* ccTLDs, so PSL can't reject them; four-part version string `4.2.1.7` read as IPv4. **XMR (Monero) not supported → 0 recall** (SilkVault posts use XMR; also note the seeded XMR addresses are 64 chars, real ones are 95).
- **Intent** (45 posts): **0.78** overall, **0.89 on unambiguous posts**. Main error: buyer/escrow replies ("taking a unit") labelled `sale` — the model keys on the thread's selling context.
- **MITRE mapping** (45 posts, parent level):
  | Strategy | P | R | F1 |
  |---|---|---|---|
  | LLM raw | 0.22 | 0.35 | 0.27 |
  | LLM verified | 0.25 | 0.35 | 0.29 |
  | Semantic only | 1.00 | 0.04 | 0.07 |
  | **Hybrid** | **0.27** | **0.38** | **0.32** |
  Threshold sweep: 0.30 → P .08 / R .12; 0.45 → P 1.00 / R .04; ≥0.50 → nothing. ⇒ cosine scores between a short forum post and a long ATT&CK description are low and compressed; the threshold can only trade one for the other.

**How to present this (important):** "Hybrid beats either alone, which validates the design, but absolute F1 is 0.32 — mapping is the weakest stage. Root causes: (a) posts are short slang, technique descriptions are long formal prose → embedding mismatch; (b) Mistral-7B over-proposes plausible T-codes for short replies (most FPs are on chit-chat). Next steps I'd take: embed ATT&CK *procedure examples* instead of descriptions, add a cross-encoder reranker, gate technique extraction on intent (skip replies), few-shot prompts, and grow the gold set with a second annotator to measure inter-annotator agreement." Verification cut raw-LLM FPs 64→53 without losing a single TP.

Translation finding (real data, posts 432/437): argostranslate dropped characters from sha256/BTC strings — direct evidence for "extract IOCs from the original body".

---

## 10. PRODUCT PASS (Oct 2026): redesign, discovery, watchlists

### UI: Swiss / "evidence register" redesign (PRODUCT.md records the direction)
- Tokens kept, values replaced (index.css): cool paper ground, ink, ONE signal red, amber only for "degraded", Public Sans + JetBrains Mono (mono only for IOCs/T-codes), self-hosted via @fontsource.
- Removed: WebGL shader, 5.5 s boot, CRT, grain, scramble text, three.js. Routes are `React.lazy` code-split; a 2px loading bar shows only while a chunk loads; d3 loads only on graph views.
- Home opens on a real cited finding (top technique, provenance split, evidence chips) + "chain of custody" + interactive evidence web.
- Provenance is encoded by PATTERN + colour + label (solid = LLM-verified, hatched = semantic, outline = unverified) — accessible, consistent everywhere (`components/Evidence.tsx`).
- Health: "cached mode" (amber) when Ollama/Tor are off; red only if the DB is down.
- Posts: day-grouped register, one SSE stream (fixed a duplicate stream), activity chart with drag-to-filter date range, search, filters in the URL (`?f=&q=&from=&to=`), j/k/Enter, "/" to search.
- `EvidenceGraph` (d3-force): drag nodes, hover-trace neighbours, click a technique/IOC to pin its cluster, double-click IOC to pivot, keyboard-focusable nodes. Used on Home, Investigations (inline) and the full case graph.
- Global shortcuts: `g` + o/p/t/i/d/a/s, `?` sheet.
- Bugs found by the redesign: ATT&CK v18 split Defense Evasion → Stealth + Defense Impairment (heatmap hard-coded the old tactic, so all stealth techniques fell into "Other"); rules-of-hooks violation in InvestigationDetail; case-graph legend contradicted the drawing.

### Discovery (the "Robin" front half) — `backend/discovery/`
- Answers "where should I look?" before scraping. Query → optional LLM keyword refinement → engines → ranked candidates → analyst selects → generic page reader → existing pipeline.
- `LocalIndexEngine`: SQLite FTS5 (external-content table `posts_fts` + triggers, porter tokenizer, bm25 ranking, `snippet()` highlights) over everything collected. Offline, instant; this is literally what a dark-web search engine is (Ahmia = index of crawled onion pages).
- `OnionSearchEngine`: real engines (Ahmia's onion) over Tor SOCKS5; generic result parser takes every outbound .onion link incl. redirect-wrapped ones. Engines are config, not code. Fails independently (friendly "Tor proxy not reachable").
- FTS query built by quoting every user term → FTS operators in input can't break/inject the query.
- `page_reader.read_page`: layout-agnostic (drops script/nav/footer/forms, keeps title + leaf text blocks), stable 48-bit URL hash as post id → dedupe via composite key; each page's .onion host is its `source`.
- Runner "pages mode": `/scrape-jobs {urls:[…]}` (≤25, .onion-only, rate-limited) → read pages → extract → LLM → MITRE.
- Interview line: "Robin is good at FINDING; SentinelX is good at turning what's found into verified, structured, cited intelligence. Discovery joins the two."
- Ethics: read-only GETs, no forms/logins/downloads, real engines are opt-in and disabled when Tor is down.

### Watchlists + alerts — `backend/api/watch.py`
- Saved terms (company, domains, wallets) → every post mentioning one becomes a `watch_hit`. Exact-phrase FTS5 queries, incremental per-watchlist cursor (`last_post_id`), idempotent via UNIQUE(watchlist_id, raw_post_id).
- Backfill on create is marked seen (history, not 40 fake "new" alerts); only later posts alert. Checked on read + polled every 20 s; header badge shows unseen count; Discover has "Watch these words".
- Real-world equivalent: brand/domain monitoring, the #1 thing CTI tools are bought for.

Tests: 49 passing (added discovery: page reader, result parser, FTS safety, engine failure isolation, index triggers; watch: backfill/seen/incremental/cleaning; API: pages-mode validation, discover search).
