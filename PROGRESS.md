# PROGRESS.md — SentinelX I build log

Append a new dated entry every time a stage advances, a blocker is hit, or a non-obvious decision is made. Newest entry on top. See `CLAUDE.md` §4 for the handoff protocol that requires this file to stay current.

---

## 2026-04-30 (latest) — Stage 6.5: investigations layer + lens reruns + diagnostics

**Why this exists:** While reviewing Apurv Singh Gautam's [Robin](https://github.com/apurvsinghgautam/robin) (NetworkChuck-reviewed AI dark-web OSINT tool) the user asked what we should learn from it. Three patterns mapped cleanly onto SentinelX without diluting its identity (continuous monitoring, not ad-hoc search): the *Investigation* abstraction (named, replayable filter views), preset *analysis lenses* (the same data re-summarised through different analyst perspectives), and a deeper *health check* for the dashboard. Built as a "Stage 6.5" before Stage 7 frontend so the UI has richer material to render.

**Done in this session:**
- Schema: added `investigations` table (id, name, description, filters_json, lens, summary, summary_model, summary_post_ids, created_at, updated_at, last_run_at) + `idx_investigations_created`. Migration applied via `executescript` against the live DB; existing tables untouched.
- `backend/llm/lenses.py` — four lenses (`threat_intel`, `ransomware`, `personal_identity`, `corporate_espionage`) with system prompts inspired by Robin's `PRESET_PROMPTS` but rewritten to reference SentinelX's structured fields (IOCs, entities, MITRE techniques) so the LLM doesn't re-derive what we already extracted. Lens names live in code, not DB — prompt evolution doesn't require migrations.
- `backend/api/investigations.py` — service module: filter→SQL translation (`category`, `intent`, `technique`, `q`, `ioc_type`, `since`, `until`, `post_ids`; all combine with AND), filter evaluation/count, post-pack helper that renders one post + its enrichment as a compact text block for the LLM, and `run_lens_summary` that re-evaluates the filter on rerun (caps at `MAX_POSTS_PER_RERUN=20`, body clipped to `MAX_BODY_CHARS=1200`), calls Mistral via the existing `OllamaClient` with the lens system prompt, and writes summary + summary_model + summary_post_ids + last_run_at back onto the row. Filters stored verbatim as JSON; investigations are *live views*, not snapshots.
- `backend/api/main.py` — added `Body` + `Response` imports, bumped version to `0.6.5`, registered: `GET /lenses`, `GET/POST/PATCH/DELETE /investigations[/id]`, `POST /investigations/{id}/rerun`, `GET /healthz/full`. The full health probe runs four independent checks (DB, Tor SOCKS5 TCP probe, Ollama `/api/tags`, pipeline cursor backlog at each stage).
- **Verified 2026-04-30 against live DB on :8765:**
  - `/lenses` → 4 items.
  - Create investigation (`POST /investigations`) → row 1 with filter `{intent:sale, q:credential}`, lens `ransomware`. After PATCH, `/investigations/1` shows `matched_total=6` (post ids 210, 162, 148, 123, 100, 30 — all VPN-cred sales).
  - `POST /investigations/1/rerun` → 36.78s end-to-end. Mistral returned a 1744-char ransomware-lens report with `[#post_id]` citations, IOCs correctly attributed (e.g. `okta-sso.help` cited across all 6 posts; per-post ipv4s back-traced to source ids), MITRE chain grounded in techniques actually mapped to the post set (T1078, T1087, T1566). `last_run_at`, `summary_model='mistral'`, `summary_post_ids=[210,162,148,123,100,30]` persisted.
  - `/healthz/full`: db up (0ms), ollama up (696ms, mistral:latest installed), pipeline up (0/0/0 backlog), tor_socks down (Docker stack not currently running — expected).
  - Negative paths: invalid lens on POST → 400; DELETE → 204; subsequent GET → 404; bad-body curl → 400.
- **Filter SQL bug avoided during build:** original draft used `args.insert(0, ...)` for both `technique` and `ioc_type` JOIN bindings, which silently swapped them when both filters were set. Refactored to maintain `join_args` and `where_args` as separate lists, returning `join_args + where_args` so positional `?` placeholders stay aligned with their JOIN/WHERE order regardless of which filter keys are present.
- `STAGE_06_5_LEARN.md` written: Robin comparison + identity sentence (Robin = ad-hoc OSINT search; SentinelX = continuous monitoring), the *live view vs snapshot* decision, lens prompt design (why we feed structured fields not raw text), filter→SQL translation pattern, why we don't keep summary history, where this hooks into Stage 7 (saved-investigations sidebar, lens selector, health card on dashboard).

**Stage 6.5 exit checklist:**
- [x] Schema migration applied + verified.
- [x] All 8 new endpoints registered and exercised against live DB.
- [x] Real lens rerun against Mistral (not a mocked test).
- [x] Negative paths verified (404, 400, 204).
- [x] LEARN doc shipped.
- [x] CLAUDE.md §3 updated.
- [x] This PROGRESS.md entry.
- [ ] Git commit — deferred per CLAUDE.md §8.

**Notes / gotchas worth carrying forward:**
- Lens reruns are **live**: rerunning re-evaluates the filter at rerun time, so an investigation created last week against `intent=sale` will pick up newly-ingested sale posts on the next rerun. This is the right behaviour for a continuous-monitoring tool but means `summary_post_ids` is a record of *that rerun's* inputs, not the investigation's permanent membership.
- We cap rerun input at 20 posts (`MAX_POSTS_PER_RERUN`) and 1200 chars/body (`MAX_BODY_CHARS`) because Mistral's effective context starts degrading past ~8k tokens. Bigger investigations get sampled. If/when we move to a longer-context model, raise the caps in `backend/api/investigations.py`.
- The `/healthz/full` endpoint imports `OllamaClient` lazily inside the handler so cold starts don't pay for the import path when only `/healthz` is hit. Tor probe is a 2s TCP timeout — we don't actually open a circuit because doing so from the API process would dirty the scraper's port pool.
- Robin's preset prompts were not copied verbatim. Our prompts explicitly reference our schema's IOC/entity/technique structures so the LLM uses them as authoritative rather than re-deriving from text.

**Next session:** Stage 7 — React + Vite + Tailwind frontend. Per `STAGE_07_DESIGN.md` this is the dark-Avinyr-meets-Dark-Netflix design. Saved-investigations sidebar + lens selector now have a backend to talk to. Do not start without explicit user go-ahead per CLAUDE.md §3.

---

## 2026-04-30 — Stage 6 closed: FastAPI read-only backend

**Done in this session:**
- Wrote `backend/api/main.py` (~250 lines) — single-file FastAPI app over the SQLite store.
- Installed `fastapi==0.115.0` and `uvicorn[standard]==0.30.6` into `backend/.venv` (pulls starlette, httptools, watchfiles, websockets, python-dotenv).
- Architecture decisions:
  - One sqlite3 connection per process, opened in a `lifespan` async context manager and stored on `app.state.conn`. `check_same_thread=False` because uvicorn dispatches sync handlers in a threadpool. Read-only by design — the pipeline workers (their own processes, their own connections) remain the sole writers, so no contention.
  - CORS wide-open for the upcoming Vite frontend.
  - All filters parametrised SQL (`?` placeholders, args list); column names are constants.
  - `/posts/{id}` does a 5-way join (post + analysis + iocs + entities + techniques) and deserialises `targets_json` / `techniques_json` so the frontend never sees stringified JSON.
  - `/posts/{id}` techniques sorted: `llm_verified` → `semantic` → `llm_unverified`, then by score desc, then by T-code.
  - `/techniques` uses `LEFT JOIN mitre_techniques` (so unverified T-codes survive the response) plus a correlated `post_count` subquery; relies on `idx_pt_tech` for speed.
  - `/iocs` and `/entities` aggregate on the server with `GROUP_CONCAT(raw_post_id)`; the API splits the comma string back into a real `int[]` before returning so consumers don't have to.
- **Verified 2026-04-30:** all endpoints exercised against the live DB.
  - `/healthz` → `{"status":"ok"}`.
  - `/stats` → 235 posts / 235 LLM-analysed / 235 MITRE-matched / 176 IOCs / 239 entities / 697-technique corpus / 292 post_techniques (232 verified + 45 unverified + 15 semantic). Intent breakdown: discussion 113, sale 94, other 20, recruitment 8.
  - `/posts?limit=2`, `/posts?intent=sale`, `/posts?technique=T1566` (153 hits), `/posts/1` (full join, sample showed targets/techniques deserialised), `/posts/99999` → 404.
  - `/techniques?only_seen=true` → 24 techniques attached to ≥1 post (top: T1566/153, T1078/34, T1087/12).
  - `/techniques/T1566` → corpus row + 153 mapped posts.
  - `/iocs?ioc_type=ipv4` → top IPv4 23.129.64.218 (10 occurrences). `/entities?label=THREAT_ACTOR` → APT29 ×5, FIN7 ×4, APT28 ×2.
- Wrote `STAGE_06_LEARN.md` in the same voice as Stages 1–5: mental model (why the API is a separate stage; why FastAPI not Flask), file map, endpoint surface, architecture choices (single connection w/ `check_same_thread=False`, lifespan ctx mgr, CORS, offset vs cursor pagination, parametrised SQL, the LEFT JOIN reasoning, `CASE … ORDER BY` for technique ranking), worked example of `/posts/{id}` and `/stats` shapes, tech-stack tour with industry context (Splunk/Elastic/Sentinel/CrowdStrike/Recorded Future, OpenAPI codegen, CQRS-lite), what's deferred to Stage 7/8 (auth, WebSocket, PDF, graph endpoint), gotchas, end-of-stage status.
- Updated `CLAUDE.md`: header date, Stage 6 row → ✅ complete / ✅ LEARN, Stage 6 paragraph in "currently running / verified working", "What is NOT yet done" updated for Stage 7. Added gotcha #8 to §7 (Splunk owns localhost:8000 — use `--port 8765`).

**Stage exit checklist for Stage 6 (per CLAUDE.md §5):**
- [x] Code works end-to-end (verified by running uvicorn + curl-ing every endpoint against the live DB).
- [x] `STAGE_06_LEARN.md` shipped at repo root.
- [x] `CLAUDE.md` §3 updated; new gotcha added to §7.
- [x] `PROGRESS.md` entry added (this entry).
- [ ] Git commit — deferred per CLAUDE.md §8 (user commits explicitly).

**Notes / gotchas worth carrying forward:**
- Splunk Web binds 127.0.0.1:8000 on the user's machine. `uvicorn --port 8000` fails with `[Errno 13] error while attempting to bind`. Use `--port 8765`.
- `check_same_thread=False` is safe for read-only API; would need re-thinking if Stage 8 ever adds write endpoints (e.g. PDF export logging). Switching the DB to WAL mode (`PRAGMA journal_mode=WAL`) is the right move at that point.
- `_maybe_json` parses the stored `targets_json` / `techniques_json` columns on the way out so the frontend gets real objects. Storage-format-vs-API-shape decoupling pattern.
- We deliberately did *not* add SQLAlchemy, pydantic response models, auth, caching, or background tasks. Each was considered and skipped with a reason in the LEARN doc; revisit if scale or threat model changes.

**Next session:** Stage 7 — React + Vite + Tailwind frontend that consumes this API. Do not start without explicit user go-ahead per CLAUDE.md §3.

---

## 2026-04-30 — Stage 5 closed: MITRE ATT&CK ingest + vector index

**Done in this session:**
- Stage 5 code (already scaffolded prior to session) verified end-to-end:
  - `backend/mitre/ingest.py` — downloads the official MITRE Enterprise STIX 2.1 JSON to `data/mitre/enterprise-attack.json`, parses out attack-pattern objects (drops revoked/deprecated, requires an `mitre-attack` external_id T-code), resolves sub-technique parent T-codes via STIX relationship objects.
  - `backend/mitre/embed.py` — sentence-transformers wrapper around `all-MiniLM-L6-v2` (384-d, L2-normalised). float32 BLOB roundtrip via `np.frombuffer` / `tobytes`; lazy model load via `lru_cache`.
  - `backend/mitre/match.py` — two-pass matcher per post: (1) verify LLM's candidate T-codes against the corpus set → `llm_verified` or `llm_unverified`; (2) embed post body, `corpus_matrix @ post_vec` cosine, `argpartition` top-k with threshold and exclude set.
  - `backend/mitre/run.py` — CLI: `--ingest`, `--once`/`--watch`, `--reset` / `--reset-corpus`, `--topk 5`, `--threshold 0.45`, `--model`, `--limit`, `--batch 25`. Cursor uses the `post_processing_state` table introduced in Stage 4 with `stage='mitre'` rows.
  - Schema: `mitre_techniques` (PRIMARY KEY technique_id, embedding BLOB), `post_techniques (UNIQUE(raw_post_id, technique_id, source))`, `mitre_runs` audit log.
- **Verified runs (2026-04-30):**
  - `--ingest`: 697 techniques parsed, embedded in 45.0s on CPU, upserted into `mitre_techniques`.
  - `--once`: 235/235 posts processed in ~5s (after model warm-up). 232 llm_verified + 45 llm_unverified + 15 semantic = 292 rows in `post_techniques`. 160/235 posts have ≥1 technique attached. Top T-codes: T1566 Phishing (153), T1078 Valid Accounts (34), T1086 PowerShell-legacy (14), T1087 Account Discovery (12). Semantic scores cluster 0.451–0.505 (threshold 0.45).
  - Re-run `--once`: no-op (cursor working — drains nothing).
- Wrote `STAGE_05_LEARN.md` in the same voice as Stages 1–4: mental model (probabilistic Stage-4 → grounding via Stage 5), what MITRE ATT&CK is, STIX 2.1 quirks (sub-techniques as relationship objects, T-codes in external_references), embedding fundamentals (cosine vs dot product, why we normalise), why MiniLM specifically, why BLOB instead of JSON, why no FAISS at this scale, the verify+discover pass split with line refs, calibration of `topk=5` / `threshold=0.45`, full tech-stack tour with industry context (TRAM, Sentinel, CrowdStrike, Recorded Future), what's deferred, gotchas, end-of-stage status.
- Updated `CLAUDE.md` §3: header date → 2026-04-30, Stage 5 row → ✅ complete / ✅ LEARN, added Stage 5 paragraph to "What is currently running / verified working", "What is NOT yet done" updated for Stage 6.

**Stage exit checklist for Stage 5 (per CLAUDE.md §5):**
- [x] Code works end-to-end (verified by running ingest + match against the live DB).
- [x] `STAGE_05_LEARN.md` shipped at repo root.
- [x] `CLAUDE.md` §3 updated.
- [x] `PROGRESS.md` entry added (this entry).
- [ ] Git commit — deferred per CLAUDE.md §8 (user commits explicitly).

**Notes / gotchas worth carrying forward:**
- The threshold `0.45` is calibrated to our specific synthetic forum corpus. Real-world bodies (longer, more technical) may want re-tuning. CLI flag is exposed.
- Many "verified" T-codes in our results are deprecated parents (T1086, T1077, T1192, T1078). MITRE keeps them in the JSON until they're fully revoked, so they pass our corpus-membership check. Modernising these to current sub-technique IDs is deliberately deferred to Stage 6.
- First `--ingest` is dominated by the 45s embedding pass + initial sentence-transformers / HuggingFace cache populate. Subsequent ingests after `--reset-corpus` are fast.
- `urllib` 120s timeout in `ingest.download` is tight if MITRE GitHub mirror rate-limits; not seen in practice but documented in LEARN doc gotchas.

**Next session:** Stage 6 — FastAPI backend exposing raw_posts, iocs, entities, llm_analyses, post_techniques, mitre_techniques. Do not start without explicit user go-ahead per CLAUDE.md §3.

---

## 2026-04-29 — Stage 4 closed: STAGE_02/03/04_LEARN.md shipped in friendlier voice

**Done in this session:**
- Wrote `STAGE_02_LEARN.md` and `STAGE_03_LEARN.md` from scratch in the new friendlier voice (matching the Stage 1 template the user signed off on earlier today). Every technical point from the prior versions preserved — file-by-file walkthroughs, decision rationales, tech-stack tour with industry context, gotchas, hand-off contracts. Voice changes:
  - Quick orientation paragraph at the top so the reader knows what the stage is *for* before diving in.
  - Jargon unpacked inline the first time it appears (cursor, idempotent, context manager, dataclass, NER, sans-IO, B-tree, upsert, etc.).
  - "Detour" boxes for opt-in one-level-deeper questions.
  - "Try this now" boxes at natural pause points with concrete `sqlite3` queries the user can run against the live DB.
  - Bug-bounty / pentest framing pinned to specific concepts when natural (server-side validation as dedup, regex permissiveness as input-validation bug parallel, prompt injection as the LLM-era XSS, state drift as race-condition family, DNS leaks as OPSEC failure).
  - "Five things to actually remember" closing section that's framed as actionable takeaways, not a recap.
- **Wrote `STAGE_04_LEARN.md` from scratch** in the same voice. Sections: mental model (deterministic vs probabilistic stages), file walkthrough (`schema.sql`, `client.py`, `prompts.py`, `chain.py`, `run.py`), the 4-prompt design with line-level commentary on each, `_extract_json` fallback story, async fanout with `asyncio.gather` + Semaphore, the persistence upsert pattern, decision rationales (Local Mistral vs OpenAI/Claude — privacy is the load-bearing argument; Mistral 7B vs phi3-mini vs llama3-8b vs the 70b+ models that don't fit; storing JSON-as-text vs normalising; sync within a post vs async fanout), tech-stack tour with industry context, **a focused "honest" section on the async speedup story** explaining exactly why we got 1.08× instead of the 3-4× I hoped for (GPU is compute-bound on a single 4060 with a 7B model; verified via `ollama ps` reporting 100% GPU), where Stage 4 will be revisited (streaming, prompt versioning, confidence calibration, refusal handling, cost tracking, prompt-injection defence — flagged as the LLM-era XSS), explicit hand-off to Stage 5.
- Updated `CLAUDE.md` §3: Stage 4 row → ✅ complete / ✅ LEARN. Header date stays 2026-04-29. "What is NOT yet done" updated to reflect Stage 4 closure.

**Stage exit checklist for Stage 4 (per CLAUDE.md §5):**
- [x] Code works end-to-end (verified twice — sync 33.0 min and async 30.6 min, both 235/235 zero failures).
- [x] `STAGE_04_LEARN.md` written at repo root, exhaustive coverage in the friendlier voice.
- [x] `CLAUDE.md` §3 updated.
- [x] `PROGRESS.md` has this entry.
- [ ] Git commit — **deferred to user**.

**LEARN-doc voice cheat sheet** (so future sessions can match it):
1. Top: 1-paragraph "Quick orientation" describing the whole stage in plain language.
2. §0: "Mental model" + a 4-row pressures-table (the canonical CTI-stage shape).
3. File map first, then file-by-file walkthrough, longest at the heart of the stage.
4. Inline-unpack the first occurrence of jargon. **Detour** boxes for opt-in depth (PRAGMA, idempotent, context manager, B-tree, sans-IO, NER, async-vs-sync, upsert).
5. **Try this now** boxes at natural pause points — usually a `sqlite3` one-liner against the live DB.
6. Pentest framing where natural — Burp / HackerOne / OWASP analogies. The user has solid web-vuln intuition; lean into it.
7. §4: decision table "what we picked vs alternatives we didn't."
8. §5: tech-stack tour with industry context — "where does each piece show up at real CTI shops / dbt / Airflow / Postgres / etc."
9. Gotchas / dedicated focused sections for one-time deep dives (Stage 1's setpriv pattern; Stage 2's `socks5h://` story; Stage 3's PRAGMA-table-info migration; Stage 4's GPU-bottleneck honesty section).
10. Final "Five things to actually remember" section. Framed as takeaways the reader could state aloud.

**Handoff for next Claude:**
- Read CLAUDE.md (auto-loaded). Status reflects this entry. Stages 1–4 are now fully closed.
- **Stage 5 is next:** MITRE ATT&CK ingest + vector index. Do NOT start without explicit user go-ahead.
- The user has an RTX 4060 and Ollama installed — the LLM stack is GPU-accelerated. CLAUDE.md "Performance ceiling" note is still relevant.
- Do NOT auto-commit. The user has not asked for a commit since the `82470de stage 3` one.
- If the user wants more voice tweaks across the LEARN docs, apply the same change uniformly to all four. The voice cheat sheet above is the rubric.

---

## 2026-04-29 — Stage 4 async refactor + STAGE_01_LEARN.md rewrite (friendlier voice)

**Built:**
- **`AsyncOllamaClient`** added to `backend/llm/client.py` — sibling of `OllamaClient` backed by `httpx.AsyncClient`. Same surface, same defaults. Body-builder logic factored into a private `_build_body()` shared between sync and async paths.
- **`chain.analyse_post_async`** — fans out the 4 prompts via `asyncio.gather`. Single-task per prompt via `_run_one`; `_PROMPT_SPECS` tuple keeps the (name, builder, json_mode, num_predict) config in one place. The sync `analyse_post` is preserved as a public API but no longer used by `run.py`.
- **`run.py` rewritten to drive an async loop:** `_amain()` is the asyncio coroutine; `main()` calls `asyncio.run(_amain(...))`. New helpers `_analyse_one`, `process_batch_async`, `run_once_async`, `run_watch_async`. `--concurrency N` flag controls how many posts are in-flight at once via an `asyncio.Semaphore` — peak in-flight Ollama requests = concurrency × 4. Default 2.
- DB-side concurrency notes: I pre-fetch IOCs+entities for all batch rows on the sync sqlite3 connection *before* spawning tasks, so coroutines never touch sqlite concurrently. `_persist` runs back on the main loop after each completed `as_completed` future. The schema is already concurrency-safe (`llm_analyses.UNIQUE(raw_post_id)` + the `ON CONFLICT DO UPDATE` upsert in `_persist`).

**Benched (full 235-post drain, both runs zero failures, zero NULL fields):**

| Setup                                   | Wall-clock | s/post | Speedup |
|-----------------------------------------|------------|--------|---------|
| Sync (sequential prompts)               | 1979s (33.0 min) | 8.42 | baseline |
| Async, concurrency=2, 4-prompt gather   | 1837s (30.6 min) | 7.82 | **1.08x** |
| Async, concurrency=4 (smoke, 8 posts)   | 73s for 8 | 9.1 | *worse* |

**Honest assessment: the async refactor barely moved the needle.** Cause is hardware-side, not code-side. A 7B model loaded into a single 4060's 8 GB VRAM saturates the GPU on a *single* request — `ollama ps` reports `100% GPU` while one prompt is generating. There is no idle compute for parallel requests to fill, so adding concurrency just makes prompts queue and stretch each other's latency. Concurrency=4 was actively worse: too many big prompts in flight starve each other on the same GPU.

**The async work is still worth keeping** for two reasons:
1. **Live `--watch` demo path:** per-post latency drops from ~8-12s sequential to ~6-8s because the 4 prompts within one post overlap (the cheap intent prompt finishes while the longer summary is still generating). User-perceived snappiness is what matters here, not batch throughput.
2. **Future-proofing:** if the user ever moves to a multi-GPU box or a smaller model, the async path will scale; sync wouldn't.

**The CLAUDE.md §3 "Performance ceiling" note** explicitly tells future sessions not to re-attempt async-side gains. Future Stage-4 wins must come from a smaller model (`phi3:mini`, `qwen2.5:3b` — already supported via `--model` flag), a bigger GPU, or response streaming for the summary prompt.

**Also done in this session — `STAGE_01_LEARN.md` rewrite (friendlier voice):**

User asked for a tone change across all LEARN docs: same technical depth, but jargon unpacked inline, optional "Detour" boxes for one-level-deeper questions, hands-on "Try this" boxes at natural pause points, and bug-bounty framing where it helps (the user has solid web-vuln intuition from Burp/HackerOne practice but limited prod-codebase reading time, almost zero hands-on Python practice — described their work as "vibecoded" with conceptual clarity but weak muscle memory).

I rewrote `STAGE_01_LEARN.md` end-to-end as the template for the rewrite voice. Specific structural changes from the original:
- A 1-paragraph "Quick orientation" at the very top so the reader knows what the whole stage is *for* before diving in.
- Section §0 ("What you can do right now") got a "Try this now" box with `cat tor_config/hidden_service/hostname` + `docker ps`.
- Jargon inline-unpacks: PRAGMA, loopback, WSGI vs ASGI, what an index actually is (B-tree analogy + phonebook framing), bind mount vs named volume side-by-side table, why `exec` matters for SIGTERM, etc.
- Bug-bounty framing pinned to specific concepts: Jinja's autoescape vs XSS, `<int:thread_id>` URL converter vs IDOR, privilege dropping vs sudoers, "stealing the keypair = impersonating the .onion" linked back to keypair-as-identity intuition.
- "Try this" boxes added to: §0 (cat hostname / docker ps), §3.1 (curl through SOCKS5 inside the tor container), §3.7 (down -v vs hidden_service persistence demonstration).
- "Detour" boxes for opt-in depth: PRAGMA, loopback, WSGI vs ASGI, why-not-`USER`-debian-tor in Dockerfile.
- "Five things to remember" closing section that's framed as actionable takeaways, not a recap.
- No depth was cut. The §4 decision table, §5 tech-stack tour with industry context, §6 CTI-shop mapping, §7 gotchas, §8 Stage-2 preview are all still there. Only the *voice* changed.

**User has not yet read the rewritten Stage 1.** Awaiting feedback on whether the voice works before doing 02/03/04. If they want adjustments, the same adjustments will be applied across all four.

**Stage exit checklist (per CLAUDE.md §5):**
- [x] Code works end-to-end (verified twice — sync and async, 235/235 each, zero failures).
- [ ] `STAGE_04_LEARN.md` written at repo root — pending (in the new voice, after user signs off on Stage 1 rewrite).
- [x] `CLAUDE.md` §3 updated.
- [x] `PROGRESS.md` has this entry.
- [ ] Git commit — **deferred to user**.

**Handoff for next Claude:**
- Read CLAUDE.md (auto-loaded). Status reflects this entry.
- **Do not re-attempt async-side speedups for Stage 4.** The CLAUDE.md "Performance ceiling" note explains why. If the user asks for faster Stage 4, the answer is `--model phi3:mini` or `qwen2.5:3b` (already supported), not more concurrency.
- **First task on resume:** wait for the user's feedback on the rewritten `STAGE_01_LEARN.md`. If they like the voice, apply the same treatment to `STAGE_02_LEARN.md` and `STAGE_03_LEARN.md`, then write `STAGE_04_LEARN.md` from scratch in that voice.
- The async code is in `backend/llm/{client.py, chain.py, run.py}`. The sync `analyse_post` is still public API but unused by `run.py`. If anyone wonders why it's still there: it's the simpler reference implementation for STAGE_04_LEARN.md to walk through before the async overlay.
- Stage 5 (MITRE ATT&CK ingest + vector index) is next. Do NOT start without explicit user go-ahead.

---

## 2026-04-29 — Stage 4 LLM pipeline code complete + verified end-to-end on 235/235 posts

**Built:**
- `backend/llm/` package — `client.py` (thin Ollama HTTP wrapper, no `ollama` Python pkg dep), `prompts.py` (4 prompt templates with shared facts-block formatter and 4000-char body clip), `chain.py` (4-prompt orchestrator, `_extract_json` fallback for Mistral's occasional prose-around-JSON), `run.py` (CLI mirroring Stage 2/3 conventions: `--once` / `--watch` / `--reset`, plus `--limit` / `--model` / `--batch`).
- Schema additions in `backend/db/schema.sql`:
  - `llm_analyses(raw_post_id UNIQUE, summary, intent, targets_json, techniques_json, model, analysed_at, raw_responses)` with `ON CONFLICT(raw_post_id) DO UPDATE` upsert in `run._persist` so re-analysis is idempotent. FK to `raw_posts(id) ON DELETE CASCADE`.
  - `post_processing_state(raw_post_id, stage, processed_at)` — per-stage cursor table, primary-keyed on `(raw_post_id, stage)`. This is the design called out in `STAGE_03_LEARN.md` §3.3 / §7; Stages 5+ will reuse it instead of growing more columns on `raw_posts`.
  - `llm_runs` audit log mirroring `scraper_runs` / `extraction_runs`.
- Cursor: `WHERE raw_posts.processed_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM post_processing_state WHERE stage = 'llm' AND raw_post_id = rp.id)`. Stage 4 owns its own cursor; Stage 3's `processed_at` is read-only from here.
- 4-prompt chain:
  1. **Summary** — free text, `format` omitted, 400 num_predict.
  2. **Intent** — JSON `{intent, confidence, reason}`, single label from `[sale, recruitment, how-to, doxxing, discussion, other]`. `format=json`, 200 num_predict.
  3. **Targets** — JSON `{industries, geographies, victim_types}`. `format=json`, 300 num_predict.
  4. **Techniques** — JSON `{techniques: [{id, name, evidence}], behaviour: [...]}`, MITRE T-codes as candidates only (Stage 5 will verify). `format=json`, 500 num_predict.
- Each prompt receives a `KNOWN FACTS` block built from Stage-3 IOCs + entities (grouped by type/label, deduped, sorted) so the LLM doesn't waste tokens re-deriving atoms we already have ground truth for.

**Verified end-to-end (2026-04-29):**
1. **Smoke test:** `python -m backend.llm.run --once --limit 2` — both posts succeeded, 25.2s + 9.6s wall-clock, summaries coherent, JSON parses cleanly, audit + state rows correct.
2. **Full corpus drain:** `python -m backend.llm.run --once --batch 25` — 235/235 posts in ~33 minutes wall-clock on the user's RTX 4060 (Ollama reports `100% GPU`, ~5.1 GB VRAM). Average per-post: ~8s sequentially across the 4 prompts. **Zero failures, zero NULL fields across summary/intent/targets/techniques.**
3. **Intent distribution:** discussion=111, sale=92, other=17, recruitment=13, doxxing=2 — looks sane for a CTI forum.
4. `llm_runs` audit table has 11 rows (1 smoke-test run of 2 posts, then ~9 batches of 25 + a tail of 8). All clean exits.
5. Idempotency confirmed: a follow-up `--once` would no-op because every `raw_posts.id` now has a row in `post_processing_state` for `stage='llm'`.

**Hardware / runtime context:**
- User's machine: Windows 11 + RTX 4060 8 GB VRAM. Confirmed via `ollama ps`: `mistral:latest 5.1 GB 100% GPU` while running. CPU was NOT in use; my earlier explanation about CPU-bound speed was wrong and corrected mid-conversation. GPU temp held ~70 °C at ~70% utilisation throughout the run.
- Ollama version 0.22.0, `mistral:latest` (4.4 GB on disk, 5.1 GB resident).

**Known issues / deliberate non-goals:**
- **Sequential prompts per post** — the 4 prompts in `chain.analyse_post` run one after another. This is the single biggest win still available; user wants live demo to feel snappy, so next session is the async refactor (see "Deferred").
- The MITRE technique IDs in `techniques_json` are LLM guesses, not verified against the official corpus. That verification is Stage 5's job — by design.
- `chain._extract_json` has a regex fallback for Mistral's occasional prose-wrapped JSON; in the 235-post run it never had to fire (every JSON-mode response parsed via the fast-path `json.loads`). Worth keeping anyway because the failure mode is rare-but-real.

**User comprehension checks during the build (worth noting for future sessions):**
- User asked why each post takes ~12s (worried about live-demo feasibility). Walked through: 4 sequential prompts × ~3s each on GPU. After confirming `ollama ps` reports `100% GPU`, agreed that the per-post time is dominated by sequential calls, not throughput. Async refactor will close this.
- User asked whether Docker / `.onion` are needed for Stage 4. They are NOT — Stage 4 reads from local SQLite and talks only to localhost Ollama. Docker/Tor stack is only needed for Stages 1+2 (or the live-demo path that re-uses scraper+extractor in `--watch` mode).
- User concerned about whether the demo would be "fake" if pre-computed. Clarified: pre-computed != fake. Every row was genuinely produced by Mistral at some point. The "live demo" path (all 4 stages in `--watch` mode, audience watches a new post enriched in ~60-90s) is the same code, just with the timing visible. **No canned responses, no theatre, and CLAUDE.md §3 now records this as the demo plan.**

**Deferred to next session:**
- **Async / concurrency refactor** of `chain.analyse_post`. Plan: switch `OllamaClient` to `httpx.AsyncClient`; rewrite `analyse_post` to fire all 4 prompts via `asyncio.gather`; add `--concurrency N` flag to `run.py` for processing N posts in parallel. Target: 12s/post → 2-3s/post end-to-end. Schema is already concurrency-safe (`UNIQUE(raw_post_id)` + upsert).
- `STAGE_04_LEARN.md` — exhaustive teaching doc covering: file walkthrough (`llm/client.py`, `llm/prompts.py`, `llm/chain.py`, `llm/run.py`, schema additions); design rationales (why Ollama HTTP API not the Python SDK, why `format=json` + regex fallback, why per-stage `post_processing_state` table over more columns on `raw_posts`, why `KNOWN FACTS` block in every prompt, prompt design choices for each of the 4 stages, why temperature=0.2, why JSON-by-example schema pinning, why the techniques are candidates not verified); industry context (LangChain prompt-chains vs hand-rolled, OpenAI structured outputs vs Ollama's `format=json`, GPU vs CPU placement, why CTI workflows are always pre-compute + dashboard-reads).

**Stage exit checklist (per CLAUDE.md §5):**
- [x] Code works end-to-end (verified: 235/235 posts, zero failures, zero NULL fields).
- [ ] `STAGE_04_LEARN.md` written at repo root — pending.
- [x] `CLAUDE.md` §3 updated.
- [x] `PROGRESS.md` has this entry.
- [ ] Git commit — **deferred to user**.

**Handoff for next Claude:**
- Read CLAUDE.md (auto-loaded). Status reflects this entry.
- **First**, do the async/concurrency refactor of `chain.py` and re-bench against the corpus (`--reset` first, then time the drain). Then write `STAGE_04_LEARN.md`. Then mark Stage 4 ✅ in §3.
- Stage 5 (MITRE ATT&CK ingest + vector index) is next; do NOT start without an explicit go-ahead.
- The user has an RTX 4060 — GPU is available, do not assume CPU-bound.
- Do NOT auto-commit. User commits explicitly.

---

## 2026-04-29 — Stage 3 closed: STAGE_03_LEARN.md shipped

**Done:**
- Wrote `STAGE_03_LEARN.md` at the repo root, matching the depth + structure of `STAGE_02_LEARN.md`. Sections: mental model of an extraction stage (4-pressure table); file-by-file walkthrough of the schema additions, the guarded `ALTER TABLE` migration in `Store._init_schema`, `backend/pipeline/extract.py` (regex pattern set with line-by-line commentary on the IPv4 permissive-then-validate pattern, simplified IPv6, BTC legacy+bech32, URL stop-set, RFC-5322-pragmatic email; the `refang()` strategy and why we don't mutate the original body; `IOCExtractor.extract()` with the six lessons in its ordering — URL/email before domain, sha256 before sha1 before md5, span-overlap filter, canonicalisation at extract time, `Match` as a frozen dataclass; `EntityExtractor` and why we disable spaCy's parser+lemmatizer, the `_KEEP_LABELS` rationale per label, the curated MALWARE/THREAT_ACTOR pass with three alternatives weighed; `dedupe()` as application-layer perf optimisation atop DB UNIQUE correctness), `backend/pipeline/run.py` (the `WHERE processed_at IS NULL ORDER BY id` cursor and why ordering matters, `process_batch` with five points on the run-log row, the processed_at-after-inserts ordering invariant, one-commit-per-batch, partial-progress-then-raise on exception, no-streaming justification; `run_once`'s `seen < batch` termination; `reset_extractions` and the Stage-2 vs Stage-3 reset boundary). Decision rationales: regex IOCs vs LLM IOC extraction (4 reasons), spaCy `en_core_web_sm` vs md/lg/trf, processed_at column vs separate processing-state table (with explicit migration plan for Stage 4+), `dedupe()` vs DB-only UNIQUE, per-row try/except vs batch insert. Dedicated section unpacking the SQLite `ALTER TABLE … ADD COLUMN` migration story with the three options weighed and why guarded `PRAGMA table_info` is right at this scale. Tech-stack tour with industry context for spaCy, `en_core_web_sm`, Python `re`, defanging conventions, IOCs as the CTI atomic unit, MITRE ATT&CK + STIX 2.1 forward references, SQLite UNIQUE+CASCADE, `PRAGMA table_info` migrations, and an Ollama/Mistral preview. Future-revisit list (more IOC types, whitelisting, confidence scores, MITRE-derived MALWARE/THREAT_ACTOR sets, interval-tree overlap if posts grow, per-IOC-type CLI toggles, fine-tuning spaCy). Explicit Stage 3 → Stage 4 hand-off contract (immutable iocs/entities tables, `raw_posts.id` as the join key, Stage 4 owns its own cursor in a new `post_processing_state` table).
- Updated `CLAUDE.md` §3: Stage 3 row → ✅ complete / ✅ LEARN. Header date bumped to 2026-04-29. "What is NOT yet done" rewritten to remove the Stage-3-LEARN bullet and to gate Stage 4 on explicit go-ahead, with a forward pointer to `STAGE_03_LEARN.md` §3.3 / §7 for the per-stage cursor design Stage 4 will need.

**Stage exit checklist (per CLAUDE.md §5):**
- [x] Code works end-to-end (verified 2026-04-27 — 235/235 processed first run, 0/0 second run, distributions sane).
- [x] `STAGE_03_LEARN.md` written at repo root, exhaustive coverage.
- [x] `CLAUDE.md` §3 updated.
- [x] `PROGRESS.md` has this entry.
- [ ] Git commit — **deferred to user**.

**Handoff to next Claude:**
- Stage 3 is closed. Do NOT start Stage 4 without an explicit go-ahead from the user.
- Stage 4 = local LLM pipeline (Ollama + Mistral, 4-stage prompt chain). Reads from `raw_posts` + `iocs` + `entities`, joined on `raw_posts.id`. Will need its own cursor — current plan (per `STAGE_03_LEARN.md` §3.3 + §7) is to introduce a `post_processing_state(raw_post_id, stage, processed_at)` table rather than retrofit `raw_posts` with another column. The LLM prompt should *include* the existing structured facts (IOCs, entities) as context, not re-derive them.
- Prerequisite to verify before Stage 4 starts: `ollama` installed on host, mistral model pulled. The user has Ollama installed per CLAUDE.md §2 — confirm before assuming.
- Do NOT auto-commit. User commits explicitly.

---

## 2026-04-27 — Stage 3 extraction pipeline code complete + verified end-to-end

**Built:**
- `spacy==3.7.5` + `en_core_web_sm==3.7.1` installed into `backend/.venv`. Also pulled in numpy 1.26, pydantic 2, thinc, blis, etc. (transitive).
- `backend/db/schema.sql` extended with three new tables:
  - `iocs(raw_post_id, ioc_type, value, span_start, span_end, extracted_at)` with `UNIQUE(raw_post_id, ioc_type, value)` and FK to `raw_posts(id) ON DELETE CASCADE`. Indexes on `raw_post_id`, `ioc_type`, `value`.
  - `entities(raw_post_id, label, text, span_start, span_end, extracted_at)` with `UNIQUE(raw_post_id, label, text)`. Indexes on `raw_post_id`, `label`.
  - `extraction_runs(started_at, finished_at, posts_seen, iocs_inserted, entities_inserted, error)` — audit log mirroring `scraper_runs`.
- `Store._init_schema` now adds the `processed_at REAL` column to `raw_posts` via a guarded `ALTER TABLE` (SQLite has no `IF NOT EXISTS` for `ADD COLUMN`, so we read `PRAGMA table_info(raw_posts)` first). This keeps DBs created in Stage 2 forward-compatible without a migration tool.
- `backend/pipeline/extract.py`:
  - `refang()` — normalizes defanged IOCs (`[.]`/`(.)`/`{.}` → `.`, `[at]` → `@`, `hxxp(s)://` → `http(s)://`) on a working copy before regex matching.
  - `IOCExtractor` — compiled regex set for ipv4/ipv6/cve/md5/sha1/sha256/btc/url/domain/email. Matches longest-first (sha256 > sha1 > md5) using a span-overlap filter so a 64-hex string isn't triple-counted. URLs and emails consumed first so domains don't double-match. IPv4 octets validated 0-255 to drop false positives like `999.999.999.999`.
  - `EntityExtractor` — wraps spaCy with `parser` and `lemmatizer` disabled (only NER needed; parser is the slowest pipe in the small model). Keeps PERSON/ORG/GPE/NORP/PRODUCT/EVENT/LOC and discards DATE/CARDINAL/etc. as noise. Adds a curated keyword pass for MALWARE (Cobalt Strike, Conti, LockBit, ...) and THREAT_ACTOR (Lazarus Group, APT28, FIN7, ...) since spaCy doesn't know these out of the box.
  - `dedupe()` — collapses `(type, value)` duplicates within a single post before insert (DB enforces same invariant via UNIQUE; pre-collapsing avoids one IntegrityError per dup).
- `backend/pipeline/run.py`:
  - Cursor: `WHERE processed_at IS NULL ORDER BY id LIMIT ?`. Stage 3 owns its own cursor — does NOT touch Stage 2's `MAX(source_created_at)` cursor. (LEARN §7 hand-off contract honored.)
  - `process_batch` writes one `extraction_runs` row per batch, updates it on success or exception. Uses per-row `try/except IntegrityError` for inserts (same pattern as scraper).
  - `--once` drains by looping `process_batch` until a partial batch comes back. `--watch` swallows exceptions and retries on the next tick. `--reset` wipes extractions and clears `processed_at`.

**Verified end-to-end:**
1. `python -m backend.pipeline.run --once` (first run): processed 235/235 raw_posts in ~3s.
2. Result counts:
   - IOCs (176): ipv4=53, cve=43, btc=36, domain=21, email=14, sha256=9.
   - Entities (239): ORG=86, PERSON=50, NORP=24, MALWARE=23, PRODUCT=21, GPE=20, THREAT_ACTOR=13, EVENT=1, LOC=1.
3. `python -m backend.pipeline.run --once` (second run): processed=0. Cursor working.
4. Sample IOCs include legit hits: `185.220.101.45`, `CVE-2024-21413`, `bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq`, `alice@example.com`. MALWARE pass correctly grabbed Lazarus, Cl0p, Qakbot, Conti from seed text.

**Known limitations (deliberate, not bugs):**
- spaCy small model mislabels short tokens — e.g. "CVE-2024-21413" lands in ORG, "Qakbot" lands in GPE. The regex IOC layer + curated keyword passes are the corrective layer; this is exactly why we don't rely on NER alone. Real CTI shops feed NER outputs through entity-linking + custom rules; our Stage 5 (MITRE + vector index) is the closest analogue.
- No URL/IP defang in stored values yet — we refang for matching, but values written to DB are post-refang. That's correct for downstream MITRE/Stage-5 work but means we lose the original surface form. Acceptable.
- Entity dedup is exact-string within a post — "Lazarus" and "Lazarus Group" coexist. Entity linking (collapsing aliases) is Stage 5 territory.

**Deferred to next session:**
- `STAGE_03_LEARN.md` — exhaustive teaching doc covering: file walkthrough (`backend/pipeline/extract.py`, `backend/pipeline/run.py`, schema additions); design rationales (regex IOCs vs ML-only NER, defanging strategy, why span-overlap filtering for hash families, why disable spaCy parser, why curated keyword passes for MALWARE/THREAT_ACTOR, processed_at column vs separate processing-state table); industry context (NER pipelines at CTI vendors, the IOC defang convention's history, hash-format conflicts); explicit Stage 3 → Stage 4 hand-off (Stage 4 reads from raw_posts + iocs + entities to build LLM prompts).

**Handoff for next Claude:**
- Read CLAUDE.md (auto-loaded). Status reflects this entry.
- Resume by writing `STAGE_03_LEARN.md`. Then mark Stage 3 ✅ in §3.
- Stage 4 is local LLM pipeline (Ollama + Mistral); do NOT start without explicit go-ahead.
- Do NOT auto-commit. User commits explicitly.

---

## 2026-04-27 (later) — Stage 2 closed: STAGE_02_LEARN.md shipped

**Done:**
- Wrote `STAGE_02_LEARN.md` at the repo root. Sections: mental model of a CTI collection layer (4-job table); file-by-file walkthrough of `backend/db/schema.sql`, `backend/db/store.py`, `backend/scraper/client.py`, `backend/scraper/run.py` with line-level commentary on the cursor read, the per-row `try/except` dedup, the run-log context manager, and the watch-loop error-swallowing; decision rationales (cursor as `MAX(source_created_at)` vs state row, `UNIQUE + IntegrityError` vs `INSERT OR IGNORE`, SQLite vs Postgres at this stage, httpx vs requests/aiohttp, context-manager run log vs decorator); a dedicated section unpacking the `socks5h://` vs `socks5://` story for httpx; tech-stack tour with industry context for httpx, socksio, Tor SOCKS5, SQLite, cursor-based incremental ingest, audit-log tables, argparse + `python -m`; explicit Stage 2 → Stage 3 hand-off contract (immutable upstream rows, downstream-owned cursors).
- Updated `CLAUDE.md` §3: Stage 2 row → ✅ complete / ✅ LEARN. "What is NOT yet done" rewritten to reflect Stage 2 closure and gate Stage 3 on explicit go-ahead.

**Stage exit checklist (per CLAUDE.md §5):**
- [x] Code works end-to-end (verified earlier today: fetched=235 first run, fetched=0 second run, cursor pinned).
- [x] `STAGE_02_LEARN.md` written at repo root, exhaustive coverage.
- [x] `CLAUDE.md` §3 updated.
- [x] `PROGRESS.md` has this entry.
- [ ] Git commit — **deferred to user**.

**Handoff to next Claude:**
- Stage 2 is closed. Do NOT start Stage 3 without an explicit go-ahead from the user.
- Stage 3 = NER + IOC extraction. Will run over `raw_posts.body`, key results by `raw_posts.id` (not `source_post_id` — see LEARN §7). Plan: spaCy for named entities (threat actors, malware, orgs), regex for IOC types (IPv4/IPv6, CVE, MD5/SHA1/SHA256, BTC, URL/domain, email). Stage 3 owns its own cursor over `raw_posts.id` — does NOT share Stage 2's cursor.
- User has not asked for a commit. Don't commit unprompted.

---

## 2026-04-27 (later) — Stage 2 scraper code complete + verified end-to-end

**Built:**
- `backend/.venv` — Python 3.12 venv. `httpx[socks]==0.27.2` installed.
- `backend/db/schema.sql` — `raw_posts` (UNIQUE on `source_post_id` for dedup, indexes on `source_created_at`, `source_thread_id`, `category`) + `scraper_runs` (one row per scraper invocation, captures cursor before/after, fetched/inserted/duplicates counts, error string).
- `backend/db/store.py` — `Store` class. `get_cursor()` returns `MAX(source_created_at)` (no separate state row → cannot drift), `insert_posts()` does dedup via `INSERT` + `IntegrityError` catch, `run()` context manager wraps each poll in a `scraper_runs` entry that finalizes on success or exception.
- `backend/scraper/client.py` — `ForumClient` wraps an `httpx.Client` configured with `socks5://127.0.0.1:9050`. Generous timeouts (connect 30s, read 60s) because Tor circuits are slow. `read_onion_hostname()` reads `tor_config/hidden_service/hostname`.
- `backend/scraper/run.py` — `python -m backend.scraper.run` CLI with `--once` / `--watch --interval N` / `--reset-cursor` modes. Watch mode swallows fetch errors and retries on the next tick (Tor circuits flap; we don't want one timeout to kill the loop).

**Verified end-to-end against the live stack:**
1. `docker compose up --build -d` — forum healthy, tor running, .onion = `ryntxkxpk6zzeo6gtirtjwadt7hcfgd6lfmqqtut5b6l5qv7imsdapyd.onion`.
2. `python -m backend.scraper.run --once` (first run): `fetched=235 inserted=235 duplicates=0 cursor=0.0 -> 1777120704.17`.
3. `python -m backend.scraper.run --once` (second run): `fetched=0 inserted=0 duplicates=0` — cursor pinned correctly, no spurious dupes.
4. Stored rows distribute across all 5 categories (general 65 / marketplace 61 / access 38 / vulnerabilities 38 / credentials 33).
5. `scraper_runs` table has 2 rows reflecting both invocations with correct before/after cursors.

**Gotchas hit:**
1. **`socks5h://` is not accepted by httpx 0.27** — raises `Unknown scheme for proxy URL`. Despite the curl/requests convention, httpx's SOCKS5 transport (via `httpx[socks]` → `socksio`) already does proxy-side DNS by default, so plain `socks5://` works for `.onion`. Updated default in `client.py` and CLI help. Added as Gotcha #7 in CLAUDE.md §7.

**Deferred to next session:**
- `STAGE_02_LEARN.md` — exhaustive teaching doc covering: file walkthrough (`backend/db/`, `backend/scraper/`), why `MAX(source_created_at)` over a separate cursor row, why `INSERT + IntegrityError` over `INSERT OR IGNORE` (we need the count of duplicates), the `socks5h://` vs `socks5://` story, why timeouts are generous, why watch mode swallows errors, how this would extend to multi-source scraping in real CTI shops.

**Handoff for next Claude:**
- Read CLAUDE.md (auto-loaded). Status reflects this entry.
- Resume by writing `STAGE_02_LEARN.md`. Then mark Stage 2 ✅ in §3.
- Stage 3 is NER + IOC extraction; do NOT start it without an explicit go-ahead.
- Do NOT auto-commit. User commits explicitly.

---

## 2026-04-27 — Stage 1 closed: STAGE_01_LEARN.md shipped

**Done:**
- Wrote `STAGE_01_LEARN.md` at the repo root. Exhaustive deep-dive covering: file-by-file walkthrough of `onion_service/`, `tor_config/`, and `docker-compose.yml`; line-level explanation of `app.py` (per-request DB connection, `since`-based incremental polling, `fmt_ts` filter), `schema.sql` (epoch-float timestamps + indexes), `seed_data.py` (IOC pools + templates), `torrc` (every directive), and `entrypoint.sh` (root → chown → setpriv → exec pattern); decision-table of choices vs alternatives; tech-stack tour with industry context for Flask, gunicorn, SQLite, Jinja2, Tor, v3 hidden services, Docker, bind mounts vs volumes, setpriv, healthchecks; gotchas hit during the build; preview of how Stage 2 will consume Stage 1's contract.
- Updated `CLAUDE.md` §3 status table: Stage 1 row now ✅ complete / ✅ LEARN. Updated "what is NOT yet done" to reflect Stage 1 closure.

**Stage exit checklist (per CLAUDE.md §5):**
- [x] Code works end-to-end (verified 2026-04-26 — Tor bootstrapped 100%, forum healthy, .onion address stable).
- [x] `STAGE_01_LEARN.md` written at repo root, exhaustive coverage.
- [x] `CLAUDE.md` §3 updated.
- [x] `PROGRESS.md` has this entry.
- [ ] Git commit — **deferred to user**. User must say "commit this" before any commit happens (CLAUDE.md §8).

**Handoff to next Claude:**
- Stage 1 is closed. Do NOT start Stage 2 without an explicit go-ahead from the user.
- When Stage 2 starts: create `backend/.venv` (host-side Python venv) per CLAUDE.md §7 gotcha #6. The scraper lives under `backend/scraper/` and writes to a SQLite DB under `backend/db/`. It must use `socks5h://127.0.0.1:9050` (the `h` is mandatory for `.onion` resolution). The `.onion` to scrape is in `tor_config/hidden_service/hostname`.
- User has not asked for a commit. Don't commit unprompted.

---

## 2026-04-26 — Stage 1 code complete, LEARN doc deferred

**Built and verified working:**
- `onion_service/` — Flask synthetic darknet forum.
  - `app.py`: HTML routes (`/`, `/category/<slug>`, `/thread/<id>`), JSON API (`/api/posts?since=&limit=&category=`, `/api/post/<id>`), `/healthz`.
  - `schema.sql`: `threads` + `posts` (epoch float timestamps so the JSON API can do numeric `since` filtering).
  - `seed_data.py`: generates realistic threads with rich IOC content (IPs, CVEs, BTC addrs, SHA256, threat actor names, malware families) for downstream NER. Idempotent w/ `--reset` and `--seed` flags. Default seed=42 for reproducibility in container.
  - Templates `base/index/category/thread.html` + dark-theme `static/style.css` (phpBB-circa-2005 aesthetic, monospace, green-on-black).
  - `Dockerfile`: python:3.12-slim, gunicorn 2 workers, seeds DB on first boot if missing, `/data/forum.db` on a named volume.
- `tor_config/` — Tor hidden service container.
  - `torrc`: v3 hidden service forwarding `:80 -> forum:5000`, SOCKS5 on `0.0.0.0:9050`, `ClientOnly 1`.
  - `Dockerfile`: debian:bookworm-slim + `tor` + `util-linux` (for `setpriv`).
  - `entrypoint.sh`: fixes bind-mount ownership at runtime, drops privileges to `debian-tor`, exec's tor. **This was added after the first boot crash-looped on bind-mount ownership** (see Gotchas in CLAUDE.md).
- `docker-compose.yml`: forum service (no host port — only reachable through Tor) + tor service (depends_on forum healthy, exposes 9050 to host, bind-mounts `./tor_config/hidden_service` for stable .onion).
- `.gitignore`: already covered Python/SQLite/Tor keys/data dumps/PDFs.

**Verified by running:**
- `docker compose up --build` — both containers come up.
- Tor logs `Bootstrapped 100% (done): Done`.
- Forum `/healthz` returns 200 OK on the compose-internal healthcheck.
- `tor_config/hidden_service/hostname` populated with stable v3 `.onion` address.

**Gotchas hit and fixed:**
1. First build was killed mid-`apt-get install` by transient network loss (debian repo + pypi both dropped at the same moment). Rerunning the same `docker compose up --build` worked — base images were already cached.
2. First successful build crash-looped tor with `/var/lib/tor/sentinelx_forum/ is not owned by this user (debian-tor) but by root`. Root cause: Docker Desktop bind mounts ignore image-time `chown`. Fixed by switching to a runtime entrypoint that chowns + drops privileges. Documented in CLAUDE.md §7.
3. `Socks version 71 not recognized` warnings — confirmed harmless, ignored.

**Deferred to next session:**
- `STAGE_01_LEARN.md` — the exhaustive teaching doc. User stopped session before this was written; build-first/teach-after is the agreed cadence so this is genuinely just "still pending," not skipped.

**Handoff notes for the next Claude:**
- Read `CLAUDE.md` first (auto-loaded anyway). Status table is current as of this entry.
- Resume by writing `STAGE_01_LEARN.md`. Cover every file under `onion_service/`, `tor_config/`, and `docker-compose.yml`. The user wants exhaustive — don't be terse. Sections to include: what was built, how each piece works (line-level when interesting), why this choice over alternatives, the tech stack with industry context (where Flask, gunicorn, Tor hidden services, Docker bind mounts, SQLite, Jinja2 are used in real CTI tooling), and the gotchas we hit.
- After that, mark Stage 1 ✅ in CLAUDE.md §3 and proceed to Stage 2 (Tor scraper) only when the user gives the go-ahead.
- Do NOT auto-commit. User commits explicitly.

---

## 2026-05-01 — Stage 7 closed

> NOTE: this entry covers Stage 7 only. PROGRESS.md is missing entries for
> Stages 2–6 + 6.5 (the user noticed the gap in this session). CLAUDE.md §3
> is the authoritative log for the missing stages — it has been kept current
> throughout. A future session should backfill PROGRESS.md from CLAUDE.md
> when there's time.

**What landed across the three Stage 7 sessions (full picture):**

Sessions A + B (shipped 2026-04-30, documented in CLAUDE.md §3 prior to this
entry): Vite 6 + React 18 + TS + Tailwind v4 scaffold; WebGL violet plasma
shader background (`ShaderBackground.tsx`, three.js, fbm domain-warped noise
reacting to cursor + clicks); 5-phase boot sequence (`BootSequence.tsx`,
StrictMode-safe single-fire via module flags); horizontal beeswarm timeline
with SSE bootstrap (`/api/events?since_id=0`) + live arrival rings; backend
SSE endpoint (replays at `since_id=0`, 2s polling, 15s keepalive — fixed a
falsy-zero bug where `since_id or MAX(id)` resolved 0 to the latest id);
forum gained `POST /api/threads` for live demo injection.

Session C (this session, all four items shipped):

- **MITRE heatmap** at `/techniques`. Plan called for one row of 14 columns;
  refactored to 7+7 (Pre-compromise→Foothold / Operate→Objective) on user
  feedback — much more breathable. Cells shaded by `post_count` on a log
  scale (`shade()` helper, alpha 0.12–0.90). Multi-tactic techniques appear
  in every relevant column. Click cell → side panel (description + post
  list); click a post → shared `DetailPanel`. New API: `api.techniques()`,
  `api.technique(id)` plus `TechniqueListItem` / `TechniqueDetail` types.
- **Citation links** in lens summaries. New `CitationText` component splits
  on `/\[#(\d+)\]/g` and renders each match as an inline `<button>` that
  opens `DetailPanel`. New `Investigations.tsx` page (the route was a
  Placeholder before): list (left) / detail (right), create form with name +
  lens picker + intent/keyword filter, `[ RERUN ] [ DELETE ]` actions. Added
  `api.deleteInvestigation(id)`.
- **Watch indicator** in the header. New `WatchIndicator` component polls
  `/healthz/full` every 12s. Pill: `IDLE` / `PROCESSING · N q` / `DEGRADED`.
  Hover popover shows db / ollama / tor latencies + per-stage pending
  counts. Mounted in `Shell.tsx` next to `[ CRT ]`.
- **IOC pivot graph** at `/iocs/:value` (URL-encoded). Reintroduced
  `d3-force` (already in `package.json`). Center IOC node + post satellites,
  capped at 30 posts. Co-occurring IOCs across the matched set listed below
  as clickable pivot chips so the analyst can chain pivots through the
  corpus. IOC chips inside `DetailPanel` are now `<Link>`s into this view.

Polish pass (after Session C, on user request):

- **Vertical timecord (replaced the horizontal beeswarm).** User said the
  horizontal version "kind of hard to understand it's a timeline" and asked
  for something more *Dark*-inspired. Full rewrite of `Timeline.tsx`: single
  glowing violet thread runs top→bottom (gradient + outer glow), day pills
  float on the spine, posts branch alternately right ↔ left as content cards
  (id / category / time / intent badge / title / first 6 MITRE T-codes).
  Newest day on top; reads top-down like a feed. SSE bootstrap, filter
  chips, hover-for-techniques, click-for-DetailPanel, `[ REPLAY LIVE ]` —
  all preserved.
- **Live-arrival flair on the timecord.** `SpineShimmer` motion element
  travels top→bottom along the spine (1.6s) when `arrivingIds` is non-empty
  — the cord literally flashes with new signal. Each pulsing node also
  drops a fading violet comet trail (4s). Both Framer Motion, no new deps.
  (User asked about GSAP for the boot — recommended against on bundle-size +
  two-systems grounds; added these effects instead.)
- **Global text contrast.** `--color-text` lifted `#e6e3f0 → #f4f2fb`,
  `--color-text-muted` lifted `#7a7090 → #a8a0c4` (much more legible over
  the shader). Added `.grain::after` radial dim veil between the WebGL
  shader and content. Added `h1–h6 { color: var(--color-text); }` reset to
  fix a Tailwind v4 quirk where headings inherited browser-default black —
  the FeedTeaser thread titles on the Home page were *literally invisible*
  in the screenshot the user sent. Permanent fix.
- **Color polish across Home.** Hero stat cards: violet left-edge stripe +
  inner glow. Health grid: per-service color tags (db=violet, ollama=green,
  tor=cyan, pipeline=amber). Top-techniques bars: violet gradient + halo.
  Section dividers: violet→border gradient hairline. FeedTeaser cards:
  intent-tinted left borders.
- **Refactor:** extracted `DetailPanel` + `TECH_COLOR` from `Timeline.tsx`
  to `frontend/src/components/DetailPanel.tsx`. Now reused by `Heatmap`,
  `Investigations`, `IocPivot`, and `Timeline`.

**Verified by running:**
- `npx tsc -b --noEmit` clean throughout (ran after every component).
- All four routes (`/`, `/posts`, `/techniques`, `/investigations`,
  `/iocs/:value`) load against the live backend on `:8765`.
- SSE bootstrap still replays the 235+ archived posts; `[ REPLAY LIVE ]`
  triggers the spine shimmer + comet trail on the most recent node.
- Investigation create + rerun flow exercised; `[#NNN]` citations render as
  buttons and opening `DetailPanel`.
- Heatmap cells shade correctly; hovering confirms `post_count` matches the
  legend.
- IOC pivot from a `DetailPanel` IOC chip lands at the right URL and the
  d3 sim settles cleanly.

**Stage exit checklist (per CLAUDE.md §5):**
- [x] Code works end-to-end (verified above).
- [x] `STAGE_07_LEARN.md` written at the repo root, exhaustive.
- [x] `CLAUDE.md` §3 updated.
- [x] `PROGRESS.md` has this entry.
- [ ] Git commit — **deferred to user**. Note: stages 4–7 are all
  uncommitted (last commit was `cde9ba4 intial Frontend`). One commit
  bundling everything since then would be the natural pass.

**Handoff to next Claude:**
- Stage 7 is closed. Frontend is feature-complete for the demo path.
- Next: **Stage 8** (PDF export per investigation + attack-graph polish).
  Entry-point checklist is in CLAUDE.md §3.5. Do PDF export first.
- Don't touch Stage 7 work unless user explicitly asks. The vertical
  timecord, contrast tokens, color polish, and watch indicator are settled.
- The PROGRESS.md gap (missing Stages 2–6 entries) is not a blocker — those
  stages are documented in CLAUDE.md §3 and in their respective LEARN docs.
  Backfill if you have time but don't let it delay Stage 8.
- User did not ask to commit during this session; do not commit unprompted.
