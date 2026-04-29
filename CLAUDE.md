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

**Last updated:** 2026-04-29 (Stage 3 closed: code verified + STAGE_03_LEARN.md shipped)

### Stages (8 total)

| # | Name                              | Status        | LEARN doc                |
|---|-----------------------------------|---------------|--------------------------|
| 1 | Synthetic .onion forum + Tor      | ✅ complete   | ✅ `STAGE_01_LEARN.md`    |
| 2 | Tor scraper + dedup + raw_posts   | ✅ complete   | ✅ `STAGE_02_LEARN.md`    |
| 3 | NER + IOC extraction (spaCy + regex) | ✅ complete   | ✅ `STAGE_03_LEARN.md`    |
| 4 | Local LLM pipeline (Ollama/Mistral, 4 stages) | ⬜  | —                        |
| 5 | MITRE ATT&CK ingest + vector index | ⬜            | —                        |
| 6 | FastAPI backend                   | ⬜             | —                        |
| 7 | React/Vite/Tailwind frontend      | ⬜             | —                        |
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

### What is NOT yet done

- No git commit has been made for Stage 2 or Stage 3 yet. User commits explicitly (CLAUDE.md §8).
- Stage 4 — local LLM pipeline via Ollama/Mistral (4-stage prompt chain). Do NOT start without explicit user go-ahead. It will read from `raw_posts` + `iocs` + `entities` and own its own per-stage cursor (likely a new `post_processing_state(raw_post_id, stage, processed_at)` table — see `STAGE_03_LEARN.md` §3.3 / §7).

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
