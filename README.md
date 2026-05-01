# SentinelX I

End-to-end Cyber Threat Intelligence (CTI) platform: a synthetic .onion forum
is scraped through Tor, posts are enriched by a structured-extraction pipeline
(spaCy NER + regex IOCs + a 4-prompt local-LLM chain on Mistral 7B + MITRE
ATT&CK semantic mapping), persisted to SQLite, served by FastAPI, and
visualised through a React/Vite/Tailwind dashboard with a live SSE timeline,
ATT&CK heatmap, IOC pivot graph, lens-driven investigations, case-file
attack graph, and per-investigation PDF export.

**Zero paid APIs. Zero subscriptions. Runs on a single laptop with a 4060.**

> Full project report (audience: technical reviewer / judge) lives in
> [`docs/REPORT.md`](docs/REPORT.md). Per-stage deep-dives are in
> [`docs/learn/`](docs/learn/).

---

## Architecture at a glance

```
synthetic .onion forum (Flask, Docker)
         │ Tor SOCKS5 :9050
         ▼
Tor scraper ──► raw_posts (SQLite)
         │
         ▼
spaCy NER + regex IOC extractor ──► iocs, entities
         │
         ▼
Mistral 7B / Ollama  (summary → intent → targets → techniques)
         │
         ▼
MITRE ATT&CK STIX corpus + sentence-transformer embeddings
   ──► post_techniques (llm_verified · llm_unverified · semantic)
         │
         ▼
FastAPI backend  (REST + SSE + PDF export)
         │
         ▼
React/Vite/Tailwind dashboard
   timeline · heatmap · IOC pivot · investigations · case graph · PDF
```

## Quick start (local)

Prereqs: Docker Desktop, Python 3.12, Node 20+, Ollama with `mistral:latest`
pulled.

```bash
# 1. Bring up the .onion forum + Tor stack
docker compose up --build -d

# 2. Backend venv
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
backend/.venv/Scripts/python.exe -m spacy download en_core_web_sm

# 3. Run the pipeline once over fresh data (sequential)
backend/.venv/Scripts/python.exe -m backend.scraper.run --once
backend/.venv/Scripts/python.exe -m backend.pipeline.run --once
backend/.venv/Scripts/python.exe -m backend.llm.run --once
backend/.venv/Scripts/python.exe -m backend.mitre.run --ingest
backend/.venv/Scripts/python.exe -m backend.mitre.run --once

# 4. API on :8765
backend/.venv/Scripts/python.exe -m uvicorn backend.api.main:app --port 8765

# 5. Frontend on :5173
cd frontend && npm install && npm run dev
```

### Windows PDF export note

WeasyPrint needs the GTK 3 runtime DLLs on PATH. Install from
[GTK-for-Windows-Runtime-Environment-Installer](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases)
and add `C:\Program Files\GTK3-Runtime Win64\bin` to your user PATH. Reopen
your terminal so PATH is inherited.

## Hosting

- **Frontend** — deploy `frontend/` to Vercel. Set `VITE_API_BASE` to the
  Render URL.
- **Backend** — deploy via the included `render.yaml` to Render. Set
  `CORS_ORIGINS` to your Vercel domain.
- **Ollama + Tor + scraper** — run on the demo laptop only. Free GPU/Tor
  hosting essentially doesn't exist.

Step-by-step in [`docs/REPORT.md` §10](docs/REPORT.md).

## Stack

Python 3.12 · Flask · FastAPI · uvicorn · httpx[socks] · spaCy
`en_core_web_sm` · Ollama · Mistral 7B · SQLite · sentence-transformers
`all-MiniLM-L6-v2` · MITRE ATT&CK Enterprise STIX 2.1 · Tor 0.4 · Docker
Compose · React 18 · Vite 6 · TypeScript · Tailwind v4 · TanStack Query ·
Framer Motion · Three.js · d3-force · WeasyPrint · GTK 3 runtime.

## License

Project for academic use. MITRE ATT&CK data © The MITRE Corporation, used
under the [ATT&CK terms of use](https://attack.mitre.org/resources/terms-of-use/).
