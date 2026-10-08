<div align="center">

# SentinelX

### Darknet forum posts in. Explainable, MITRE ATT&CK-mapped threat intelligence out.

**Local-first · no paid APIs · every claim traced to its evidence**

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React 18](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Ollama](https://img.shields.io/badge/Ollama-Mistral_7B-000000?logo=ollama&logoColor=white)](https://ollama.com/)
[![MITRE ATT&CK](https://img.shields.io/badge/MITRE-ATT%26CK-c8102e)](https://attack.mitre.org/)
[![Tor](https://img.shields.io/badge/Tor-Onion_Services-7E4798?logo=torproject&logoColor=white)](https://www.torproject.org/)
[![CI](https://img.shields.io/badge/tests-49_passing-2ea44f)](.github/workflows/ci.yml)

**Live demo:** [sentinal-x-two.vercel.app](https://sentinal-x-two.vercel.app) ·
**API health:** [sentinelx-api-fzk4.onrender.com/healthz](https://sentinelx-api-fzk4.onrender.com/healthz)

</div>

---

## Contents

- [Why it exists](#why-it-exists)
- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Design principles](#design-principles)
- [Quick start](#quick-start)
- [Testing and evaluation](#testing-and-evaluation)
- [Security posture](#security-posture)
- [Repository layout](#repository-layout)
- [API reference](#api-reference)
- [Hosting](#hosting)
- [Known limits](#known-limits)
- [Tech stack](#tech-stack)
- [License and attribution](#license-and-attribution)

---

## Why it exists

Breaches are often announced on darknet forums before the victim knows: network access
for sale, credential dumps, exploit code, malware rentals. Threat-intelligence analysts
read these forums by hand. That is slow, repetitive, multilingual, and does not scale.

SentinelX automates the reading. It scrapes `.onion` forums over a real Tor circuit,
extracts indicators, asks a local LLM what each post is about, maps the behaviour to
MITRE ATT&CK, and serves the result in a live analyst console — with every conclusion
linked back to the text that produced it.

---

## What it does

| | Feature | Detail |
|---|---|---|
| 🧅 | **Real Tor collection** | Two synthetic forums run as genuine Tor v3 onion services. The scraper reaches them through SOCKS5, exactly as it would a real forum. |
| 🔎 | **Indicator extraction** | Regex finds IPs, domains, URLs, emails, CVEs, hashes and crypto addresses — including defanged forms like `hxxp://evil[.]com`. spaCy finds people, organisations and places. |
| 🌐 | **Multilingual** | Non-English posts are detected and translated offline. Indicators come from the original text; the LLM reads the translation. |
| 🤖 | **Local LLM analysis** | Mistral 7B (via Ollama) writes a summary, classifies intent (sale, recruitment, doxxing, …), names targets and proposes ATT&CK techniques. |
| 🎯 | **Verified ATT&CK mapping** | LLM proposals are checked against the official MITRE corpus; an embedding search adds techniques the LLM missed. Each mapping carries its provenance. |
| 🛡️ | **Mitigations** | Defensive advice is a direct lookup of MITRE's own mitigation objects. No generated security advice. |
| 📡 | **Live feed** | New posts stream to the dashboard over Server-Sent Events, with resume-on-reconnect. |
| 🗺️ | **ATT&CK heatmap** | All 14 enterprise tactics, log-scaled, with drill-down to the posts behind each cell. |
| 🔗 | **IOC pivot & evidence graph** | Jump from any indicator to every post that mentions it. A force-directed graph shows posts, indicators and techniques; shared evidence pulls posts into clusters. |
| 🗂️ | **Investigations & lenses** | Save a filter as a case. An analyst "lens" fuses the matching posts into one report with `[#id]` citations to the source posts. |
| 📄 | **PDF export** | Cover page, cited summary, ATT&CK coverage chart and an appendix of cited posts. |
| 🧭 | **Discover & Scout** | Search for candidate dark-web pages (read by a generic page reader), or paste a forum's `.onion` URL and watch the full pipeline run on it as a background job. |
| 🔔 | **Watchlists & alerts** | Save terms (a company name, a product); matching posts raise alerts. |
| 🌍 | **UI languages** | English, Russian and Spanish. |

---

## How it works

Six stages. Each one reads from the database, does one job, and writes back.

```
 ① SOURCE    Two synthetic darknet forums served as real Tor onion services (Docker)
                 DarkBay   — JSON API              → SOCKS5 127.0.0.1:9050
                 SilkVault — HTML only, no API     → SOCKS5 127.0.0.1:9051
      │
 ② SCRAPE    backend/scraper     Tor SOCKS5 → .onion → raw_posts
      │
 ③ EXTRACT   backend/pipeline    language detect + offline translation
             backend/lang        regex IOCs (original text) · spaCy NER (English text)
      │                          → iocs, entities
 ④ LLM       backend/llm         Mistral 7B via Ollama, schema-validated JSON
      │                          summary · intent · targets · candidate T-codes → llm_analyses
 ⑤ MITRE     backend/mitre       verify LLM T-codes against the ATT&CK corpus
      │                          + MiniLM embedding similarity → post_techniques
 ⑥ SERVE     backend/api         FastAPI: REST · SSE · PDF · investigations · watchlists
                                 → React dashboard (frontend/)
```

### Who decides what

| Job | Tool | Why |
|---|---|---|
| Exact patterns (IPs, CVEs, hashes, wallets) | **Regex** | Deterministic and explainable. A language model can invent a hash. |
| Names (people, organisations, places) | **spaCy NER** | A statistical model handles varied wording. |
| Meaning: summary, intent, targets | **Mistral 7B** | Needs real language understanding. |
| ATT&CK mapping | **LLM proposes → corpus verifies → embeddings discover** | The LLM brings recall and reasoning; the corpus catches invented IDs; embeddings add grounded, scored matches. |
| Defensive advice | **MITRE lookup** | Security advice should come from the source, not be generated. |

### Technique provenance

Every post→technique link is labelled with where it came from:

| Label | Meaning |
|---|---|
| `llm_verified` | The LLM proposed it and the ID exists in the official ATT&CK corpus. |
| `semantic` | Found by cosine similarity (MiniLM, threshold 0.45) between the post and technique descriptions. No LLM involved. |
| `llm_unverified` | The LLM proposed an ID that does not exist. Kept and shown, flagged as a likely hallucination. |

---

## Design principles

1. **Local-first.** Tor, Ollama, spaCy, MiniLM and SQLite all run on one machine. Darknet text and personal data never go to a third-party API, nothing costs money, and results are reproducible.
2. **Idempotent, resumable stages.** Each stage keeps its own cursor. Re-running any stage is always safe; a crash loses nothing.
3. **Provenance everywhere.** Each output points at its evidence: the regex span, the spaCy span, the stored raw LLM response, the cosine score, the `[#id]` citation.
4. **Graceful degradation.** Translation fails → use the original. Ollama is down → indicators and semantic ATT&CK mapping still run. PDF engine missing → a clear error while the rest of the API stays up.

---

## Quick start

**Prerequisites:** Docker Desktop · Python 3.12 · Node 20+ · [Ollama](https://ollama.com) with `mistral:latest` pulled.

> The repo ships a pre-enriched demo database (441 posts), so steps 1 and 3 are optional
> if you only want to explore the dashboard.

```bash
# 1 — Start the two .onion forums and their Tor daemons
docker compose up --build -d

# 2 — Backend environment
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
backend/.venv/Scripts/python.exe -m spacy download en_core_web_sm

# 3 — Run the pipeline over fresh data (each stage is safe to re-run)
backend/.venv/Scripts/python.exe -m backend.scraper.run  --once
backend/.venv/Scripts/python.exe -m backend.pipeline.run --once
backend/.venv/Scripts/python.exe -m backend.llm.run      --once
backend/.venv/Scripts/python.exe -m backend.mitre.run    --ingest   # first run only
backend/.venv/Scripts/python.exe -m backend.mitre.run    --once

# 4 — API on :8765  (interactive docs at /docs)
backend/.venv/Scripts/python.exe -m uvicorn backend.api.main:app --port 8765

# 5 — Dashboard on :5173
cd frontend && npm install && npm run dev
```

Open <http://localhost:5173>. On macOS/Linux, use `backend/.venv/bin/python` in place of `backend/.venv/Scripts/python.exe`.

The onion addresses are generated on first start and persist across rebuilds:

```bash
cat darknet/darkbay/tor/hidden_service/hostname
cat darknet/silkvault/tor/hidden_service/hostname
```

<details>
<summary><b>PDF export on Windows</b></summary>

WeasyPrint needs the GTK 3 runtime. Install it from
[GTK-for-Windows-Runtime-Environment-Installer](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases),
add `C:\Program Files\GTK3-Runtime Win64\bin` to your user `PATH`, and reopen the terminal.
Without it, `/export` returns a clear "GTK runtime missing" error.
</details>

<details>
<summary><b>Offline translation</b></summary>

The first non-English post in each language triggers a one-time download of that
language's offline translation package (~100 MB, cached by argostranslate). After that,
translation runs with no network access.
</details>

<details>
<summary><b>Environment variables</b></summary>

| Variable | Default | Purpose |
|---|---|---|
| `SENTINELX_DB_PATH` | `backend/db/sentinelx.db` | SQLite database location |
| `SENTINELX_API_KEY` | unset | If set, POST/PATCH/DELETE require an `X-API-Key` header |
| `SENTINELX_RATE_LIMIT` | `10` | Per-IP requests/minute for expensive endpoints |
| `CORS_ORIGINS` | localhost | Allowed frontend origins |
| `SENTINELX_TOR_PROXY` | `socks5://127.0.0.1:9050` | Tor SOCKS5 proxy used by Discover |
| `VITE_API_BASE` | `http://localhost:8765` | Frontend → API base URL |
| `VITE_API_KEY` | unset | Frontend copy of the API key |
</details>

---

## Testing and evaluation

```bash
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
backend/.venv/Scripts/python.exe -m pytest              # 49 tests; no Ollama or Tor needed
backend/.venv/Scripts/python.exe -m backend.eval.run    # writes backend/eval/EVAL_REPORT.md
```

- **Tests** cover IOC extraction (defanging, overlaps, false-positive filters), the store
  (per-forum identity, cursors, migrations), ATT&CK matching maths, LLM output validation
  and prompt-injection fencing, discovery, watchlists, and the API (auth, rate limiting).
  CI runs them plus a frontend type-check on every push.
- **Evaluation** measures accuracy rather than asserting it: IOC precision/recall on
  synthetic cases with known answers, intent accuracy, and ATT&CK mapping
  precision/recall/F1 for LLM-only vs semantic-only vs hybrid, plus a threshold sweep.

---

## Security posture

- **Prompt injection.** Scraped text is fenced as untrusted data in every prompt, look-alike
  fence markers inside posts are neutralised, and every LLM response is schema-validated
  (closed intent labels, bounded sizes) before storage.
- **Write protection.** Optional API key on all mutating endpoints; expensive endpoints
  (scrape jobs, lens reruns) are rate-limited per IP.
- **Live feed integrity.** SSE frames carry IDs, so a reconnecting browser resumes via
  `Last-Event-ID` with no gaps or duplicates.
- **Secrets.** Onion private keys and databases other than the demo DB are git-ignored.

---

## Repository layout

```
SentinelX/
├── backend/                     Python 3.12 · one package per pipeline stage
│   ├── scraper/                 ② Tor SOCKS5 clients — JSON API (DarkBay) and generic HTML (SilkVault)
│   ├── lang/                    ③ language detection + offline translation
│   ├── pipeline/                ③ regex IOC + spaCy entity extraction
│   ├── llm/                     ④ Ollama client, prompt chain, output schemas, analyst lenses
│   ├── mitre/                   ⑤ ATT&CK ingest, MiniLM embeddings, hybrid matching
│   ├── api/                     ⑥ FastAPI app, investigations, watchlists, PDF export
│   ├── discovery/               search engines + page reader feeding the pipeline
│   ├── jobs/                    background runner for on-demand scrape jobs (Scout)
│   ├── eval/                    accuracy evaluation + gold set + report
│   ├── db/                      SQLite schema, data-access layer, demo database
│   ├── requirements.txt         full pipeline dependencies
│   ├── requirements-api.txt     API-only dependencies (for the hosted image)
│   └── requirements-dev.txt     test dependencies
│
├── frontend/                    React 18 · Vite · TypeScript · Tailwind v4
│   └── src/
│       ├── pages/               one file per route (Home, Timeline, Heatmap, Investigations, …)
│       ├── components/          shared UI (evidence graph, detail panel, header, backdrop, …)
│       ├── lib/                 API client, colour palette
│       └── i18n/                en / ru / es translations
│
├── darknet/                     ① the synthetic data source
│   ├── darkbay/
│   │   ├── forum/               Flask forum with a JSON API + seed data
│   │   └── tor/                 Tor daemon publishing it as an onion service
│   └── silkvault/
│       ├── forum/               structurally different forum, HTML only
│       └── tor/                 its own independent Tor daemon
│
├── tests/                       pytest suite
├── data/mitre/                  ATT&CK STIX dump + embedding cache (regenerated, git-ignored)
├── docker-compose.yml           brings up both forums and both Tor daemons
├── Dockerfile.api               API image with PDF libraries for hosting
├── render.yaml                  Render deploy blueprint
├── PRODUCT.md                   product brief: users, purpose, positioning
└── .github/workflows/ci.yml     tests + type-check on every push
```

---

## API reference

Full interactive docs are served at `/docs` (OpenAPI 3.1). Main routes:

| Method | Route | Purpose |
|---|---|---|
| GET | `/healthz`, `/healthz/full` | Liveness; full status of DB, Ollama, Tor and pipeline backlog |
| GET | `/stats` | Corpus totals for the console |
| GET | `/posts`, `/posts/{id}` | Filtered post list; one post with all its evidence |
| GET | `/events` | Server-Sent Events stream of new posts |
| GET | `/techniques`, `/techniques/{id}` | ATT&CK heatmap data; one technique with its posts and mitigations |
| GET | `/iocs`, `/entities` | Indicator and entity search (IOC pivot) |
| GET/POST/PATCH/DELETE | `/investigations[/{id}]` | Saved cases |
| POST | `/investigations/{id}/rerun` | Re-run the lens summary |
| GET | `/investigations/{id}/export` | PDF report |
| GET | `/lenses` | Available analyst lenses |
| GET/POST | `/scrape-jobs[/{id}]` | Start and track an on-demand onion scrape |
| GET/POST | `/discover/engines`, `/discover/search` | Dark-web page discovery |
| GET/POST/DELETE | `/watchlists[/{id}]` | Watch terms |
| GET/POST | `/alerts`, `/alerts/seen` | Alerts raised by watchlists |

---

## Hosting

| Component | Where | Notes |
|---|---|---|
| React dashboard | **Vercel** (root `frontend/`) | Free |
| FastAPI + SQLite | **Render** via [`render.yaml`](render.yaml) | Free; ~30 s cold start |
| Ollama + Mistral | Local machine | No free GPU hosting |
| Tor + onion forums | Local machine (Docker) | Free platforms block Tor |

The plain Python runtime on Render lacks WeasyPrint's native libraries, so PDF export
fails there. [`Dockerfile.api`](Dockerfile.api) installs them with only the API's
dependencies; point a Render Docker service at it to get PDF export in production.

---

## Known limits

- **Synthetic data.** The forums are realistic but generated; real forums add CAPTCHAs, logins and rate limits.
- **Draft gold labels.** The evaluation gold set needs human review before its numbers are quoted.
- **SQLite.** Fine for thousands of posts on one machine; a multi-user deployment would move to PostgreSQL and a job queue.
- **Small local model.** Mistral 7B is fast and private but weaker than frontier models; the verification layer exists because of that.

---

## Tech stack

**Backend** — Python 3.12 · FastAPI · uvicorn · httpx[socks] · spaCy `en_core_web_sm` ·
Ollama · Mistral 7B · sentence-transformers `all-MiniLM-L6-v2` · langdetect ·
argostranslate · SQLite (FTS5) · WeasyPrint

**Frontend** — React 18 · Vite 6 · TypeScript · Tailwind v4 · React Router ·
TanStack Query · react-i18next · Framer Motion · d3-force · EventSource (SSE)

**Infrastructure** — Docker Compose · Tor 0.4 · Flask + gunicorn · Render · Vercel · GitHub Actions

**Standards** — MITRE ATT&CK Enterprise (STIX 2.1) · Server-Sent Events · OpenAPI 3.1

---

## Author

**Srivathsa H Honyal** · BITS Pilani

## License and attribution

Project code is released for academic and non-commercial use.

MITRE ATT&CK® data © The MITRE Corporation, used under the
[ATT&CK terms of use](https://attack.mitre.org/resources/terms-of-use/).
Mistral 7B weights are governed by the [Mistral AI licence](https://mistral.ai/news/announcing-mistral-7b/).
