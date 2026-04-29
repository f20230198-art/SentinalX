# PROGRESS.md — SentinelX I build log

Append a new dated entry every time a stage advances, a blocker is hit, or a non-obvious decision is made. Newest entry on top. See `CLAUDE.md` §4 for the handoff protocol that requires this file to stay current.

---

## 2026-04-29 (latest) — Stage 3 closed: STAGE_03_LEARN.md shipped

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
