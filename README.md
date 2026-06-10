<div align="center">

# SentinelX

### End-to-end Cyber Threat Intelligence platform — local-first, no paid APIs.

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React 18](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![Vite 6](https://img.shields.io/badge/Vite-6-646CFF?logo=vite&logoColor=white)](https://vitejs.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Tailwind v4](https://img.shields.io/badge/Tailwind-v4-38BDF8?logo=tailwindcss&logoColor=white)](https://tailwindcss.com/)
[![Ollama](https://img.shields.io/badge/Ollama-Mistral_7B-000000?logo=ollama&logoColor=white)](https://ollama.com/)
[![MITRE ATT&CK](https://img.shields.io/badge/MITRE-ATT%26CK-c8102e)](https://attack.mitre.org/)
[![Tor](https://img.shields.io/badge/Tor-Hidden_Service-7E4798?logo=torproject&logoColor=white)](https://www.torproject.org/)

**Live demo:** [sentinal-x-two.vercel.app](https://sentinal-x-two.vercel.app)
**API:** [sentinelx-api-fzk4.onrender.com/healthz](https://sentinelx-api-fzk4.onrender.com/healthz)

</div>

---

## Overview

SentinelX turns unstructured darknet forum posts into **explainable, MITRE-mapped,
exportable threat intelligence**. The full pipeline — Tor scraper, IOC extraction,
local LLM enrichment, MITRE ATT&CK mapping, REST/SSE API, and React dashboard — runs
on a single machine with no paid services.

Two synthetic `.onion` forums are hosted as real Tor hidden services so the scraper
exercises a genuine SOCKS5 circuit. Every claim about a post traces back to a regex
match, a spaCy span, a specific LLM prompt, or a cosine score — provenance is
preserved end to end.

> **Zero paid APIs. Zero subscriptions. Single-laptop deploy.**

---

## Pipeline

```
   .onion forum (Flask, Docker)
            │   Tor SOCKS5 :9050
            ▼
        Tor daemon (Docker)
            │
            ▼
   ┌─ Tor scraper ────────► raw_posts
   │      │
   │      ▼
   │  Language detect + offline MT (langdetect + argostranslate)
   │      │  non-English bodies translated to English for analysis
   │      ▼
   │  spaCy NER + regex IOCs ─────────► iocs, entities
   │      │
   │      ▼
   │  Mistral 7B (Ollama)
   │   summary → intent → targets → T-codes ─► llm_analyses
   │      │
   │      ▼
   │  MITRE ATT&CK
   │   (LLM-verify  +  MiniLM cosine top-k) ──► post_techniques
   │      │
   ▼      ▼
   FastAPI  ────► REST · SSE · PDF export · investigations + lenses
        │
        ▼
   React + Vite + Tailwind dashboard
   ├── /posts             vertical timecord, live SSE, filter chips
   ├── /techniques        14-tactic ATT&CK heatmap
   ├── /investigations    saved filters + lens summaries with citations
   ├── /investigations/:id/graph    case-file d3-force graph
   ├── /iocs/:value       IOC pivot + co-occurring artefacts
   └── /investigations/:id/export   styled PDF (WeasyPrint)
```

---

## Features

| Module | What it does |
|---|---|
| **Tor hidden services** | Two real `.onion` v3 forums (DarkBay, SilkVault) over real SOCKS5 circuits. Same code paths would scrape a real darknet forum. |
| **Multilingual ingestion** | Non-English posts (ru / es / zh / …) are detected and translated to English offline (argostranslate). IOCs are extracted from the original body; NER and LLM analysis run on the translation. |
| **Idempotent pipeline** | Every stage has its own cursor. Re-running `--once` is always safe. |
| **Explainable enrichment** | Every claim about a post traces back to a regex match, a spaCy span, a specific LLM prompt, or a cosine score. No black box. |
| **On-demand scout** | Paste any `.onion` URL into the dashboard. A background job runs scrape → extract → LLM → MITRE end-to-end and streams progress. |
| **Live SSE timeline** | New posts appear in the dashboard within seconds, with shimmer + comet-trail animation. |
| **ATT&CK heatmap** | All 14 enterprise tactics, log-scaled cells, drill-down to per-technique post lists. |
| **IOC pivot** | Force-directed satellite graph for any IOC + co-occurring-IOC pivot chips. |
| **Investigation lenses** | 4 system-prompt lenses fuse a filtered post set into a single narrative with `[#NNN]` citations. Live-view, not snapshot — filters re-evaluate on every read. |
| **MITRE mitigations** | Defensive recommendations resolved from MITRE's own `course-of-action` objects — pure lookup, no LLM guessing. |
| **Case-file attack graph** | One canvas with three node kinds (post / IOC / MITRE) where shared artefacts dedupe to single nodes — co-occurrence pulls clusters. |
| **PDF export** | WeasyPrint HTML→PDF with cover, footnoted summary, MITRE coverage chart, per-cited-post appendix. |
| **UI i18n** | Console UI available in English, Russian, and Spanish (react-i18next). |
| **Watch indicator** | Header pill polls `/healthz/full` every 12s — db / Ollama / Tor / pipeline-backlog visibility. |

---

## Quick start (local)

**Prereqs:** Docker Desktop, Python 3.12, Node 20+, Ollama with
`mistral:latest` pulled.

```bash
# 1 — Bring up the .onion forums + Tor stack
docker compose up --build -d

# 2 — Backend venv + deps
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
backend/.venv/Scripts/python.exe -m spacy download en_core_web_sm

# 3 — Run the pipeline once over fresh data
backend/.venv/Scripts/python.exe -m backend.scraper.run  --once
backend/.venv/Scripts/python.exe -m backend.pipeline.run --once
backend/.venv/Scripts/python.exe -m backend.llm.run      --once
backend/.venv/Scripts/python.exe -m backend.mitre.run    --ingest
backend/.venv/Scripts/python.exe -m backend.mitre.run    --once

# 4 — API on :8765
backend/.venv/Scripts/python.exe -m uvicorn backend.api.main:app --port 8765

# 5 — Frontend on :5173
cd frontend && npm install && npm run dev
```

Then open <http://localhost:5173>.

### Windows PDF export

WeasyPrint needs the **GTK 3 runtime** on PATH. Install from
[GTK-for-Windows-Runtime-Environment-Installer](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases)
and add `C:\Program Files\GTK3-Runtime Win64\bin` to your user PATH.
Reopen the terminal so PATH is inherited. Without GTK, the `/export`
endpoint returns a clear "GTK runtime missing" 500.

### Multilingual ingestion

Language detection (`langdetect`) and translation (`argostranslate`) run as part of
the extraction step. The first non-English post in each language triggers a one-time
download of the offline OPUS-MT package for that language (~100 MB each, cached
under the user's argostranslate data directory). Subsequent posts translate offline
with no further network access.

---

## Hosting

| Component | Where | Free? |
|---|---|---|
| React frontend | **Vercel** (Vite preset, root `frontend/`) | ✓ |
| FastAPI + SQLite | **Render** (one-click via [`render.yaml`](render.yaml)) | ✓ (cold-start ~30s) |
| Ollama + Mistral | **Local machine** (GPU) | — no free GPU hosting |
| Tor + .onion forums | **Local machine** (Docker) | — free PaaS providers ban Tor |

---

## Tech stack

**Backend** — Python 3.12 · FastAPI · uvicorn · httpx[socks] · spaCy
`en_core_web_sm` · Ollama · Mistral 7B · SQLite · sentence-transformers
`all-MiniLM-L6-v2` · langdetect · argostranslate · WeasyPrint · pydyf · GTK 3 runtime

**Frontend** — React 18 · Vite 6 · TypeScript · Tailwind v4 (with
`@theme` tokens) · react-router-dom · TanStack Query · react-i18next ·
Framer Motion · Three.js · d3-force · EventSource (SSE)

**Infrastructure** — Docker Compose · Tor 0.4 · Flask + gunicorn (forums)
· Render (API hosting) · Vercel (frontend hosting)

**Standards** — MITRE ATT&CK Enterprise (STIX 2.1) · Server-Sent Events
(WHATWG) · OpenAPI 3.1

---

## Repository layout

```
.
├── README.md
├── render.yaml                     ← one-click Render deploy
├── docker-compose.yml              ← Tor + .onion forum stack
├── backend/
│   ├── requirements.txt
│   ├── api/                        ← FastAPI app · investigations · PDF export
│   ├── scraper/                    ← Tor SOCKS5 scraper (JSON + generic HTML)
│   ├── pipeline/                   ← IOC + NER extraction
│   ├── lang/                       ← language detection + offline translation
│   ├── llm/                        ← Mistral 7B prompt chain (Ollama)
│   ├── mitre/                      ← ATT&CK ingest, embeddings, matching
│   ├── jobs/                       ← on-demand pipeline job runner
│   └── db/                         ← SQLite schema, store, demo DB
├── frontend/
│   ├── vercel.json
│   ├── .env.example
│   └── src/                        ← pages, components, hooks, i18n
├── onion_service/                  ← synthetic darknet forum #1 (DarkBay)
├── onion_service_silkvault/        ← synthetic darknet forum #2 (SilkVault)
├── tor_config/                     ← Tor daemon Dockerfile + config (DarkBay)
├── tor_config_silkvault/           ← Tor daemon Dockerfile + config (SilkVault)
└── data/mitre/                     ← (regenerated) ATT&CK STIX dump + embeddings
```

---

## Author

**Srivathsa H Honyal** · BITS Pilani

---

## License & attribution

Project code is released for academic and non-commercial use.

MITRE ATT&CK® data © The MITRE Corporation, used under the
[ATT&CK terms of use](https://attack.mitre.org/resources/terms-of-use/).

The `mistral:latest` model weights are governed by the
[Mistral AI licence](https://mistral.ai/news/announcing-mistral-7b/).
