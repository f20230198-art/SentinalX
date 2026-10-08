# M00 — The Big Picture (read this first, twice)

> Goal: after this module you can explain SentinelX to anyone in 30 seconds, 2 minutes, or 5
> minutes, and you know exactly what each later module plugs into.
> Source of truth: `study/PROJECT_BRAIN.md`.

---

## 1. The problem (why does this project exist?)

Companies get breached. Very often, **before** the breach is public, someone on a darknet
forum is already:
- selling **network access** ("RDP, domain admin, US healthcare, 0.4 BTC"),
- dumping **credentials** ("5M combos, 92% valid"),
- trading an **exploit** ("PoC for CVE-2024-3400, unauth RCE"),
- advertising **malware-as-a-service** ("FUD loader, builder + support").

A defender's **threat-intelligence analyst** reads these forums by hand to catch the early
warning. That is slow, repetitive, language-limited (Russian, Chinese…), and doesn't scale.

**SentinelX automates the reading, structuring and explaining.** Raw forum text in →
structured, searchable, *explainable* intelligence out.

### Three words you must be able to define instantly
| Term | One-line meaning | Example |
|---|---|---|
| **CTI** | Evidence-based knowledge about threats that helps defenders act | "FIN7-style actor selling VPN creds for ACME" |
| **IOC** | A concrete artefact that signals compromise | `185.220.101.45`, `CVE-2024-3400`, a sha256, a BTC address |
| **TTP / MITRE ATT&CK** | *How* the attacker behaves; ATT&CK is the public catalogue of those behaviours (T-codes) | T1566 = Phishing |

> Why both IOCs and TTPs? IOCs are precise but **brittle** (attacker changes an IP in seconds).
> TTPs describe behaviour, which is **expensive for the attacker to change**. A good CTI
> product gives both. (David Bianco's "Pyramid of Pain".)

---

## 2. What it does — in the user's words

An analyst opens the dashboard and can:
1. Watch posts **arrive live** on a timeline (SSE).
2. Click a post → see the **LLM summary**, **intent** (sale / recruitment / doxxing…), the
   **IOCs** found, **entities** (malware, actors, orgs), the **MITRE techniques** mapped, and
   **MITRE-recommended mitigations**.
3. See a **heatmap** of which ATT&CK tactics/techniques dominate the corpus.
4. **Pivot** on any IOC ("which other posts mention this BTC address?").
5. Create an **investigation** (saved filter, e.g. "ransomware + sale") and run a **lens**
   (an analyst persona) that writes one fused report with **`[#id]` citations** back to posts.
6. See a **case graph** (posts ↔ IOCs ↔ techniques; shared items cluster posts together).
7. **Export a PDF** report.
8. Paste **any `.onion` URL** (Scout) and watch the whole pipeline run on a forum it has never seen.

---

## 3. How it does it — the 6 stages (memorise this shape)

```
 ① SOURCE      two synthetic darknet forums as REAL Tor hidden services (Docker)
 ② SCRAPE      Python scraper → Tor SOCKS5 → .onion  → SQLite  raw_posts
 ③ EXTRACT     language detect/translate → regex IOCs → spaCy entities
 ④ LLM         local Mistral-7B: summary · intent · targets · candidate T-codes
 ⑤ MITRE       verify LLM T-codes vs real corpus  +  embedding similarity discovery
 ⑥ SERVE       FastAPI (REST + SSE + PDF)  →  React dashboard
```

Mnemonic: **S-S-E-L-M-S** → *Source, Scrape, Extract, LLM, MITRE, Serve.*

### Four design principles (your "architecture philosophy" answer)
1. **Local-first, zero paid APIs.** Privacy (you don't send darknet/PII text to a third party)
   + cost + reproducibility. Everything: Tor, Ollama, spaCy, MiniLM, SQLite.
2. **Idempotent, resumable stages.** Every stage has its own cursor; re-running is always safe.
3. **Explainable / provenance everywhere.** Every output points back to its evidence:
   regex span, spaCy span, stored raw LLM response, cosine score, `[#id]` citation.
4. **Graceful degradation.** Translation fails → use original. Ollama down → still do IOCs +
   semantic MITRE. PDF engine missing → clear error, API still up.

---

## 4. Who does what (the 3 "brains" and why each is used)

| Job | Tool | Why this and not the others |
|---|---|---|
| Exact patterns (IPs, CVEs, hashes, BTC) | **Regex** | Deterministic, fast, 100% explainable; an LLM would hallucinate a hash |
| Names (people, orgs, places) | **spaCy NER** | Statistical model; robust to wording |
| Understanding / summarising / intent | **Mistral-7B LLM** | Needs language understanding & generation |
| Mapping to ATT&CK | **LLM proposes → corpus verifies → embeddings discover** | LLM = recall + explanation, but hallucinates IDs; embeddings = grounded & measurable |
| Defensive advice | **Pure lookup of MITRE mitigations** | Trustworthy; no LLM guesswork on security advice |

> **Rule of thumb you can say in the interview:** *"Use deterministic code where the answer is
> a pattern, a model where it's a judgement, and always verify the model against ground truth."*

---

## 5. Scripts to say out loud

### 30-second version
"SentinelX is a threat-intelligence platform that automatically reads darknet forums. It
scrapes `.onion` forums over real Tor, pulls out indicators like IPs, CVEs and crypto
addresses, uses a local Mistral LLM to summarise each post and guess the attacker's intent,
maps the behaviour to MITRE ATT&CK, and shows everything in a live React dashboard with
graphs and PDF reports. It all runs locally with no paid APIs."

### 2-minute version (use this as your opening)
1. **Problem** — analysts read forums by hand; slow, multilingual, doesn't scale.
2. **Pipeline** — "Four stages after collection: extract, enrich with a local LLM, map to
   ATT&CK, serve." Name Tor, spaCy, Mistral via Ollama, MiniLM, FastAPI, React.
3. **The interesting engineering** (pick 2–3 to expand if asked):
   - hybrid ATT&CK mapping (LLM proposals verified against the official corpus + embedding search),
   - idempotent per-stage cursors,
   - multilingual: translate for the LLM, but extract IOCs from the original,
   - provenance & citations, graceful degradation.
4. **Honest limits** — synthetic data, no labelled eval yet, SQLite scale; "here's how I'd fix".

### 5-minute walk-through order (if they say "show me")
Home (health pills) → Timeline (live SSE, filter, click a post) → DetailPanel (summary, IOCs,
techniques with `llm_verified`/`semantic`/`llm_unverified` colours, mitigations) → Heatmap →
Investigations (lens + citation chips) → Case Graph → Scout (paste onion, fast mode) → PDF.

---

## 6. 15-minute interview time budget (suggested)
| Min | What |
|---|---|
| 0–2 | 2-min pitch (above) |
| 2–6 | Architecture deep-dive on the **pipeline** (they will interrupt — let them) |
| 6–10 | Pick the CTO's curiosity: Tor, LLM/embeddings, or API/SSE |
| 10–13 | Trade-offs, weaknesses, scale, what you'd do next |
| 13–15 | Your questions for them (and CHRO: what you learned, teamwork, ownership) |

**Strategy for depth questions:** answer in 3 layers — *what* (one sentence) → *how* (the
mechanism) → *why / trade-off* (what you rejected and what breaks). CTOs probe the third layer.

---

## 7. Self-check (answer out loud without looking)
1. Define CTI, IOC, TTP, ATT&CK in one line each.
2. Name the 6 stages in order.
3. Why regex for IOCs but an LLM for summaries?
4. Which of the four principles protects you from LLM hallucination?
5. What are the numbers in your demo DB? (441 posts · 697 techniques · 580 mappings · 44 mitigations)

Answers: see sections 1, 3, 4. If you can do all five → say **"M01"**.
