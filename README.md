<div align="center">

# SentinelX I

### An end-to-end Cyber Threat Intelligence platform — built locally, runs free.

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React 18](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![Vite 6](https://img.shields.io/badge/Vite-6-646CFF?logo=vite&logoColor=white)](https://vitejs.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Tailwind v4](https://img.shields.io/badge/Tailwind-v4-38BDF8?logo=tailwindcss&logoColor=white)](https://tailwindcss.com/)
[![Ollama](https://img.shields.io/badge/Ollama-Mistral_7B-000000?logo=ollama&logoColor=white)](https://ollama.com/)
[![MITRE ATT&CK](https://img.shields.io/badge/MITRE-ATT%26CK-c8102e)](https://attack.mitre.org/)
[![Tor](https://img.shields.io/badge/Tor-Hidden_Service-7E4798?logo=torproject&logoColor=white)](https://www.torproject.org/)

**🌐 Live demo:** [sentinal-x-two.vercel.app](https://sentinal-x-two.vercel.app)
**🔌 API:** [sentinelx-api-fzk4.onrender.com/healthz](https://sentinelx-api-fzk4.onrender.com/healthz)
**📄 Technical report:** [`docs/REPORT.md`](docs/REPORT.md)
**📚 Per-stage deep-dives:** [`docs/learn/`](docs/learn/)

</div>

---

## What it does

SentinelX I takes unstructured darknet forum posts and turns them into
**explainable, MITRE-mapped, exportable threat intelligence** — every step
running on a single laptop with no paid APIs.

A synthetic `.onion` forum is hosted as a real Tor hidden service. A
scraper pulls posts through the Tor SOCKS5 circuit. Each post is enriched
by a four-stage pipeline:

1. **spaCy NER + regex** extract IOCs (IPs, domains, hashes, CVEs, BTC
   wallets, emails) and named entities.
2. **Mistral 7B (Ollama)** runs a 4-prompt chain — summary → intent →
   targets → techniques — with the structured Stage-3 facts fed back in
   as authoritative context, so the LLM doesn't re-derive what we already
   know.
3. **MITRE ATT&CK matcher** does both LLM-verification (T-codes the LLM
   claimed, checked against the corpus) and semantic discovery (cosine
   top-k against 384-d MiniLM embeddings of all 697 enterprise techniques).
4. Everything lands in **SQLite**, with one row in `post_techniques` per
   mapping, tagged `llm_verified` / `llm_unverified` / `semantic` so the
   provenance is never lost.

A FastAPI backend serves it over REST + SSE; a React/Vite/Tailwind
dashboard surfaces it as a live timeline, an ATT&CK heatmap, an IOC
pivot graph, lens-driven analyst investigations with `[#post]` citation
links, a case-file attack graph (d3-force), and on-demand styled PDF
reports rendered by WeasyPrint.

> **Zero paid APIs. Zero subscriptions. Single-laptop deploy.**

---

## Architecture

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

For the full block diagram, schema, and design rationale see
[**`docs/REPORT.md`**](docs/REPORT.md).

---

## Features

| Module | What it does |
|---|---|
| **Tor hidden service** | Real `.onion` v3, real SOCKS5 circuit. Same code paths would scrape a real darknet forum. |
| **Idempotent pipeline** | Every stage has its own cursor. Re-running `--once` is always safe. |
| **Explainable enrichment** | Every claim about a post traces back to a regex match, a spaCy span, a specific LLM prompt, or a cosine score. No black box. |
| **Live SSE timeline** | New posts appear in the dashboard within seconds, with shimmer + comet-trail animation. |
| **ATT&CK heatmap** | All 14 enterprise tactics, log-scaled cells, drill-down to per-technique post lists. |
| **IOC pivot** | Force-directed satellite graph for any IOC + co-occurring-IOC pivot chips. |
| **Investigation lenses** | 4 system-prompt lenses fuse a filtered post set into a single narrative with `[#NNN]` citations. Live-view, not snapshot — filters re-evaluate on every read. |
| **Case-file attack graph** | One canvas with three node kinds (post / IOC / MITRE) where shared artefacts dedupe to single nodes — co-occurrence pulls clusters. |
| **PDF export** | WeasyPrint HTML→PDF with cover, footnoted summary, MITRE coverage chart, per-cited-post appendix. |
| **Watch indicator** | Header pill polls `/healthz/full` every 12s — db / Ollama / Tor / pipeline-backlog visibility. |

---

## Quick start (local)

**Prereqs:** Docker Desktop, Python 3.12, Node 20+, Ollama with
`mistral:latest` pulled.

```bash
# 1 — Bring up the .onion forum + Tor stack
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

### Windows PDF export note

WeasyPrint needs the **GTK 3 runtime** on PATH. Install from
[GTK-for-Windows-Runtime-Environment-Installer](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases)
and add `C:\Program Files\GTK3-Runtime Win64\bin` to your user PATH.
Reopen the terminal so PATH is inherited. Without GTK, the `/export`
endpoint returns a clear "GTK runtime missing" 500.

---

## Hosting

| Component | Where | Free? |
|---|---|---|
| React frontend | **Vercel** (Vite preset, root `frontend/`) | ✓ |
| FastAPI + SQLite | **Render** (one-click via [`render.yaml`](render.yaml)) | ✓ (cold-start ~30s) |
| Ollama + Mistral | **Demo laptop** (GPU) | — no free GPU hosting |
| Tor + .onion forum | **Demo laptop** (Docker) | — free PaaS providers ban Tor |

Click-by-click walkthrough in [`docs/REPORT.md` §10](docs/REPORT.md).

---

## Demo flow (judge-ready)

The full verbatim narrative — what to click, what to say, in what order
— lives in [`docs/REPORT.md` §8](docs/REPORT.md). Short version:

1. Open the **public Vercel dashboard** — show 236 enriched posts, the
   timeline, the heatmap, an investigation with citations, the case
   graph, the PDF export.
2. Switch to the **demo laptop** — open the synthetic .onion forum in
   Tor Browser. Post a new thread live (BTC wallet, CVE, IP, domain).
3. Run the four pipeline stages locally — scraper → extract → LLM →
   MITRE. ~30–45 seconds end-to-end.
4. Refresh the dashboard. The new post is there, fully enriched.

Total runtime: 5–7 minutes.

---

## Build stages

Each stage shipped with an exhaustive deep-dive (what / how / why /
industry parallels) at [`docs/learn/`](docs/learn/).

| # | Stage | Deep-dive |
|---|---|---|
| 1 | Synthetic .onion forum + Tor hidden service | [`STAGE_01_LEARN.md`](docs/learn/STAGE_01_LEARN.md) |
| 2 | Tor scraper with cursor-based dedup | [`STAGE_02_LEARN.md`](docs/learn/STAGE_02_LEARN.md) |
| 3 | spaCy NER + regex IOC extraction | [`STAGE_03_LEARN.md`](docs/learn/STAGE_03_LEARN.md) |
| 4 | 4-prompt Mistral 7B enrichment chain | [`STAGE_04_LEARN.md`](docs/learn/STAGE_04_LEARN.md) |
| 5 | MITRE ATT&CK ingest + semantic mapping | [`STAGE_05_LEARN.md`](docs/learn/STAGE_05_LEARN.md) |
| 5.5 | MITRE mitigations (defensive recommendations) | [`STAGE_05_5_LEARN.md`](docs/learn/STAGE_05_5_LEARN.md) |
| 6 | FastAPI backend (REST + SSE) | [`STAGE_06_LEARN.md`](docs/learn/STAGE_06_LEARN.md) |
| 6.5 | Investigations + lenses + diagnostics | [`STAGE_06_5_LEARN.md`](docs/learn/STAGE_06_5_LEARN.md) |
| 7 | React/Vite/Tailwind dashboard | [`STAGE_07_LEARN.md`](docs/learn/STAGE_07_LEARN.md) |
| 8 | PDF export + case-file attack graph | [`STAGE_08_LEARN.md`](docs/learn/STAGE_08_LEARN.md) |

---

## Tech stack

**Backend** — Python 3.12 · FastAPI · uvicorn · httpx[socks] · spaCy
`en_core_web_sm` · Ollama · Mistral 7B · SQLite · sentence-transformers
`all-MiniLM-L6-v2` · WeasyPrint · pydyf · GTK 3 runtime

**Frontend** — React 18 · Vite 6 · TypeScript · Tailwind v4 (with
`@theme` tokens) · react-router-dom · TanStack Query · Framer Motion ·
Three.js · d3-force · EventSource (SSE)

**Infrastructure** — Docker Compose · Tor 0.4 · Flask + gunicorn (forum)
· Render (API hosting) · Vercel (frontend hosting)

**Standards** — MITRE ATT&CK Enterprise (STIX 2.1) · Server-Sent Events
(WHATWG) · OpenAPI 3.1

---

## Repository layout

```
.
├── README.md                       ← this file
├── render.yaml                     ← one-click Render deploy
├── docker-compose.yml              ← Tor + .onion forum stack
├── docs/
│   ├── REPORT.md                   ← full technical report (judge audience)
│   └── learn/                      ← 9 stage deep-dives
├── backend/
│   ├── requirements.txt            ← consolidated deps
│   ├── api/                        ← FastAPI app · investigations · PDF export
│   ├── scraper/ pipeline/ llm/ mitre/
│   └── db/                         ← SQLite schema, store, demo DB
├── frontend/
│   ├── vercel.json
│   ├── .env.example
│   └── src/                        ← pages, components, hooks, lib
├── onion_service/                  ← synthetic darknet forum (Flask)
├── tor_config/                     ← Tor daemon Dockerfile + config
└── data/mitre/                     ← (regenerated) ATT&CK STIX dump + embeddings
```

---

## Author

**Srivathsa H Honyal** · BITS Pilani

Built April–May 2026 as a learning project. Every stage is documented
and verifiable; nothing in this repo is faked, mocked, or pre-baked.

---

## License & attribution

Project code is for academic / learning use.

MITRE ATT&CK® data © The MITRE Corporation, used under the
[ATT&CK terms of use](https://attack.mitre.org/resources/terms-of-use/).

The `mistral:latest` model weights are governed by the
[Mistral AI licence](https://mistral.ai/news/announcing-mistral-7b/).
