# SentinelX I

Automated cyber threat intelligence platform that scrapes darknet forums, analyzes posts with a local LLM pipeline, maps behavior to MITRE ATT&CK, and produces actionable reports — all running free and local.

## What's in this repo right now

- [`TECHNICAL_PRIMER.md`](TECHNICAL_PRIMER.md) — full technical walkthrough of every concept, tool, and design choice used in this project. **Start here.**
- Project scaffolding (empty directories for each component — code being built stage by stage).

## Build stages

1. Synthetic `.onion` darknet forum (Flask hidden service)
2. Tor scraper (PySocks + stem)
3. spaCy NER pre-processor
4. 4-stage Mistral 7B pipeline (Ollama)
5. SQLite storage + search
6. FastAPI backend
7. React frontend (attack graph, timeline, report library)
8. PDF export (WeasyPrint)

## Stack

Python · Flask · FastAPI · spaCy · Ollama · Mistral 7B · SQLite · sentence-transformers · MITRE ATT&CK · React · Vite · Tailwind · vis.js · WeasyPrint · Tor

Zero paid APIs. Zero subscriptions.
