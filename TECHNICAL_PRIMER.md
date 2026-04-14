# SentinelX I — Complete Technical Primer

> A ground-up walkthrough for someone who has CS fundamentals but hasn't touched most of this stack.
> Read this once, skim it twice, and you'll understand **what** you're building, **how** each piece works, and **why** each choice was made.

---

## Table of Contents

1. [What Is SentinelX, In One Paragraph](#1-what-is-sentinelx-in-one-paragraph)
2. [The Domain: Cyber Threat Intelligence (CTI)](#2-the-domain-cyber-threat-intelligence-cti)
3. [The Dark Web, Tor, and Hidden Services](#3-the-dark-web-tor-and-hidden-services)
4. [The Synthetic Darknet Forum: What, Why, How](#4-the-synthetic-darknet-forum-what-why-how)
5. [Scraping Through Tor](#5-scraping-through-tor)
6. [Natural Language Processing with spaCy (NER)](#6-natural-language-processing-with-spacy-ner)
7. [Local LLMs: Ollama and Mistral 7B](#7-local-llms-ollama-and-mistral-7b)
8. [Prompt Engineering & Structured Output](#8-prompt-engineering--structured-output)
9. [The MITRE ATT&CK Framework](#9-the-mitre-attck-framework)
10. [Vector Search / Semantic Retrieval](#10-vector-search--semantic-retrieval)
11. [Databases: SQLite](#11-databases-sqlite)
12. [The Backend API: FastAPI](#12-the-backend-api-fastapi)
13. [The Frontend: React + Vite + Tailwind](#13-the-frontend-react--vite--tailwind)
14. [Graph Visualization: vis.js](#14-graph-visualization-visjs)
15. [PDF Generation: WeasyPrint](#15-pdf-generation-weasyprint)
16. [System Design: Full File Structure](#16-system-design-full-file-structure)
17. [Data Flow: A Single Post's Journey](#17-data-flow-a-single-posts-journey)
18. [Glossary of Every Term](#18-glossary-of-every-term)

---

## 1. What Is SentinelX, In One Paragraph

SentinelX is an **automated threat intelligence platform**. Security teams at big companies spend hours every day reading underground hacker forums looking for early warnings — "someone is selling access to our network," "a new exploit for our software just dropped," "our employees' passwords are being traded." SentinelX watches these forums automatically, uses AI to figure out what each post means, maps it to industry-standard threat categories, and produces a polished report the security team can act on — all in seconds. You're building the entire pipeline end to end, including a fake darknet forum to scrape from (because scraping real ones is legally risky).

---

## 2. The Domain: Cyber Threat Intelligence (CTI)

### What CTI actually is
Cyber Threat Intelligence is **information about threats, turned into decisions**. Raw data ("IP 5.6.7.8 was seen scanning port 22") becomes intelligence when it's processed, contextualized, and tells you what to do ("block this IP — it belongs to a known ransomware group targeting healthcare").

### The three levels of CTI
| Level | Audience | Example |
|---|---|---|
| **Strategic** | Executives | "Ransomware attacks on pharma are up 40% this quarter" |
| **Operational** | SOC managers | "Group X is currently targeting cloud auth endpoints" |
| **Tactical** | SOC analysts, engineers | "Block hash `a1b2c3…`, it's LockBit 3.0" |

SentinelX produces **operational and tactical** intel.

### Why darknet forums matter
The lifecycle of a cyberattack usually has a "chatter" phase before the attack:
1. Someone sells **initial access** to a company (stolen VPN creds, hacked RDP).
2. Someone else sells a **tool** (malware, exploit kit).
3. An **affiliate** buys both and executes the attack.

If you detect step 1 or 2, you can stop step 3. Darknet forums are where steps 1 and 2 happen publicly.

### Key CTI concepts you'll see everywhere
- **IOC (Indicator of Compromise):** a concrete artifact — IP, domain, file hash, email. "If you see this, you've been hit."
- **TTP (Tactics, Techniques, Procedures):** *how* an attacker operates, not *what* they use. TTPs are harder to change, so detecting them is more durable than detecting IOCs.
- **Threat Actor:** a person, group, or nation-state doing attacks. E.g., APT28 (Russia), Lazarus (North Korea), FIN7 (financial crime).
- **Campaign:** a coordinated set of attacks by one actor against one target set.
- **CVE (Common Vulnerabilities and Exposures):** a global ID for a software vulnerability. Format: `CVE-2024-12345`.

---

## 3. The Dark Web, Tor, and Hidden Services

### What "the dark web" really is
There are three layers of the internet:
- **Surface web:** indexed by Google. ~4% of all content.
- **Deep web:** not indexed (your Gmail, bank dashboard, internal wikis). ~90%.
- **Dark web:** requires special software to access. Tiny, but notorious.

The dark web is just websites that are only reachable through anonymity networks. **Tor** is the biggest one.

### What Tor is
Tor = **The Onion Router**. A network of ~7,000 volunteer-run servers ("relays"). When you use Tor:

1. Your traffic is wrapped in **three layers of encryption** (like an onion).
2. It bounces through three random relays: **entry → middle → exit**.
3. Each relay peels off one layer and only knows the previous/next hop — no single relay knows both you and your destination.

This gives you **anonymity**: the destination server sees the exit relay's IP, not yours.

```
[You] → [Entry relay] → [Middle relay] → [Exit relay] → [Destination]
  \_____encrypted thrice____/___twice___/__once___/
```

### Hidden services (.onion sites)
A normal Tor user hides themselves but visits normal websites. A **hidden service** hides the *server* too. Both client and server are anonymous.

Hidden services have addresses ending in `.onion`. Example: `facebookcorewwwi.onion` (yes, Facebook runs one). They're not in DNS — they're cryptographic identifiers.

### How a hidden service works (simplified)
1. Server picks several Tor relays to act as **introduction points**.
2. Server publishes its `.onion` address + intro points to Tor's directory.
3. Client looks up the `.onion` → gets intro points.
4. Client and server meet at a **rendezvous point** (a third relay), neither knows the other's real IP.

### How do you run a hidden service?
Surprisingly simple. You install Tor, edit its config file (`torrc`), add:

```
HiddenServiceDir /var/lib/tor/my_service/
HiddenServicePort 80 127.0.0.1:5000
```

That's it. Tor will:
- Generate a private key + `.onion` hostname the first time it starts.
- Forward traffic from port 80 on the `.onion` to your local port 5000.

**No special hosting, no domain, no IP, nothing to buy.** The `.onion` hostname is derived from the public key of a keypair Tor generates.

### The SOCKS proxy
When you want to *access* Tor from your own code (not a browser), you talk to Tor's **SOCKS5 proxy** on `127.0.0.1:9050`. SOCKS is a generic "proxy anything through me" protocol. Point any TCP client at it and traffic goes through Tor.

### Running Tor on Windows — your options
| Option | Complexity | Speed | Isolation |
|---|---|---|---|
| **Tor Browser bundle** (easiest) | 1/10 | Fast | None |
| **Standalone Tor daemon** (recommended for us) | 3/10 | Fast | Good |
| **WSL (Linux subsystem)** | 5/10 | Fast | Great |
| **Docker** | 6/10 | Medium | Best |

We'll use the **standalone Tor daemon for Windows** — a single `tor.exe` + a config file. It exposes port 9050 (SOCKS) and our hidden service.

---

## 4. The Synthetic Darknet Forum: What, Why, How

### What "synthetic" means
A **fake forum we host ourselves**, designed to look and behave like a real darknet forum. It runs as a Tor hidden service, so it has a real `.onion` address and must be accessed through Tor — the plumbing is 100% identical to scraping a real darknet site.

### Why not scrape a real one?
- **Legal:** Accessing some dark web content can violate computer misuse laws even if you don't interact. Credentials/CSAM/etc. can taint your machine and legal standing.
- **Ethical:** You don't want to send traffic that financially supports those sites.
- **Reliability:** Real darknet sites go offline constantly. Your demo would break.
- **Reproducibility:** Graders/interviewers need the same data every time.

A synthetic forum solves all four while teaching you every real technique.

### What it'll look like
Plain HTML forum: threads, posts, users. Think phpBB circa 2005 — ugly, functional, dark-themed.

Categories:
- **Marketplace** — "FUD stealer for sale $300"
- **Credentials** — "5M Netflix combos fresh"
- **Access** — "RDP access US healthcare Fortune 500, $5000"
- **Vulnerabilities** — "0day in XYZ router, PoC below"
- **General Chat** — noise to make it realistic

Each post has: title, author, timestamp, body with embedded IOCs (IPs, hashes, BTC addresses, CVEs).

### Options for building it
| Option | Pros | Cons |
|---|---|---|
| **Flask + Jinja2 templates** ← we're using this | Tiny codebase, no build step, easy to serve via Tor | Has to render server-side |
| **Static HTML files** | Simpler | No `/api/posts?since=` endpoint for scraper |
| **FastAPI + HTML** | Consistent with main backend | Overkill, more deps |
| **Django** | Free admin panel to add posts | Massive for what we need |

**We pick Flask.** Reasons: smallest possible footprint for a throwaway service, built-in templating, takes 20 lines to boot, and it's the Python standard for "tiny web thing."

### How the scraper will find posts
Two modes:
1. **HTML scraping:** fetch `/thread/42`, parse with BeautifulSoup. Realistic.
2. **JSON API:** fetch `/api/posts?since=<ts>`. Faster, cleaner.

We'll build both. Default to the API, keep HTML parsing as a demo/fallback.

---

## 5. Scraping Through Tor

### The scraper's job
A daemon that:
1. Connects to the `.onion` forum through Tor.
2. Polls for new posts every N seconds.
3. Deduplicates (SHA-256 hash of content).
4. Writes new posts to `raw_posts` table with `status='pending'`.

A separate worker picks up `pending` posts and runs the AI pipeline. Decoupling means the scraper can't crash the pipeline and vice versa.

### Libraries
| Library | Role |
|---|---|
| **`requests`** | HTTP client. Python's standard. |
| **`requests[socks]`** / **`PySocks`** | Adds SOCKS proxy support so `requests` can route through Tor's 9050 port. |
| **`stem`** | Official Tor controller library. Lets you talk to Tor itself (e.g., "give me a new circuit"). |
| **`beautifulsoup4`** | HTML parsing (tree-walking, CSS selectors). |
| **`lxml`** | Fast parser backend for BeautifulSoup. |

### The core code pattern

```python
import requests

proxies = {
    'http':  'socks5h://127.0.0.1:9050',
    'https': 'socks5h://127.0.0.1:9050',
}
resp = requests.get('http://our-address.onion/api/posts', proxies=proxies, timeout=30)
```

The `socks5h` (vs `socks5`) tells the client **"resolve DNS through the proxy too."** Critical for `.onion` hostnames — your local DNS has no idea what `.onion` means.

### Deduplication
Posts might appear in multiple lists or get edited. To avoid re-processing:

```python
content_hash = hashlib.sha256(post_body.encode()).hexdigest()
# INSERT OR IGNORE on UNIQUE(content_hash)
```

### Rate limiting & politeness
Even on our own forum, we simulate real-world behavior: 1 request per 3–10 seconds with jitter. In production this matters because darknet sites ban aggressive scrapers fast.

---

## 6. Natural Language Processing with spaCy (NER)

### What NER is
**Named Entity Recognition** = finding proper nouns / structured things in text:

> "APT28 dropped **LockBit** on a US **hospital** using **CVE-2024-1709**, contact **bc1qxy...**"

NER tags: `APT28 → THREAT_ACTOR`, `LockBit → MALWARE`, `hospital → INDUSTRY`, `CVE-2024-1709 → CVE`, `bc1qxy... → BTC_WALLET`.

### Why we use NER *before* the LLM
Three reasons:
1. **Accuracy.** LLMs hallucinate. An IP like `192.168.1.1` might become `192.168.11` in an LLM's output. Regex gets it exact.
2. **Speed.** Regex is microseconds. LLM calls are seconds.
3. **Cost.** Even local LLMs consume CPU/GPU. Do cheap work cheap.

### spaCy basics
**spaCy** is an industrial-grade NLP library (C-compiled, fast, production-ready). It ships pre-trained models:
- `en_core_web_sm` (15 MB) — small, fast. Detects PERSON, ORG, GPE (country/city), DATE, MONEY, etc.
- `en_core_web_md` / `_lg` — bigger, more accurate. We don't need them.

Core concepts:
- **`nlp` object:** the pipeline. `nlp("text here")` returns a `Doc`.
- **`Doc`:** a processed text with tokens, sentences, entities.
- **`token.ent_type_`:** entity label of a token.
- **`EntityRuler`:** lets you add rule-based patterns (e.g., "match any of these threat actor names exactly").

### Our NER stack
We combine three layers:

1. **Regex for exact-match IOCs** — fastest, most accurate for strings with rigid formats:
   - IPv4: `\b(?:\d{1,3}\.){3}\d{1,3}\b`
   - CVE: `CVE-\d{4}-\d{4,7}`
   - SHA256: `\b[a-fA-F0-9]{64}\b`
   - Bitcoin: `\bbc1[a-z0-9]{39,59}\b`

2. **EntityRuler with custom lists** — for known-but-finite vocab:
   - Threat actors: `APT1, APT28, Lazarus, FIN7, Conti, ...` (pulled from MITRE data)
   - Malware: `Emotet, TrickBot, Cobalt Strike, LockBit, ...`

3. **spaCy's built-in NER** — for fuzzy categories like industries/organizations:
   - `ORG`, `GPE` entities from `en_core_web_sm`.

Output is a JSON blob: `{"ips": [...], "cves": [...], "actors": [...], ...}`. This gets fed to the LLM as "here's what I already found, don't waste cycles finding these again."

---

## 7. Local LLMs: Ollama and Mistral 7B

### What a local LLM is
A large language model that runs on *your machine* instead of calling OpenAI/Anthropic. Trade-off: slower, less capable than GPT-4, but **free, private, offline, unlimited**.

### What Mistral 7B is
- Open-weights LLM released by Mistral AI (French startup).
- **7 billion parameters** — sweet spot for running on consumer GPUs / even CPUs.
- Comparable quality to GPT-3.5 for structured tasks like classification and extraction.
- License: Apache 2.0 (commercial use OK).

### What Ollama is
Ollama = **"Docker for LLMs."** A single tool that:
- Downloads models (`ollama pull mistral`).
- Runs them as a local HTTP server on `http://localhost:11434`.
- Exposes a simple API: `POST /api/generate` with `{"model": "mistral", "prompt": "..."}`.
- Handles quantization, memory, GPU offloading automatically.

Why Ollama vs alternatives:
| Tool | Pros | Cons |
|---|---|---|
| **Ollama** ← we use | One-command setup, JSON API, cross-platform | Less configurable |
| **llama.cpp** | Most control, smallest deps | C++ build, manual model mgmt |
| **LM Studio** | GUI | Not scriptable well |
| **vLLM** | Fastest, prod-ready | Linux + GPU only |

### Quantization (important!)
Running a 7B model at full precision needs ~28 GB RAM. We use **quantization** — compressing weights from 16-bit floats to 4-bit integers. Ollama's default is `Q4_K_M`: ~4 GB model size, ~6 GB RAM at runtime, tiny quality loss.

### How we call Mistral

```python
import requests
resp = requests.post('http://localhost:11434/api/generate', json={
    'model': 'mistral',
    'prompt': '...',
    'format': 'json',     # forces valid JSON output
    'stream': False,
    'options': {'temperature': 0.2}
})
result = resp.json()['response']  # string, but valid JSON
```

**`temperature`**: randomness. 0 = deterministic, 1 = creative. We use 0.2 for extraction (want repeatable), maybe 0.5 for the report (want it to read well).

**`format: "json"`**: Ollama constrains output to be parseable JSON. Huge reliability win.

---

## 8. Prompt Engineering & Structured Output

### Why prompts matter
A 7B model is smart but easily confused. Good prompts reduce hallucinations dramatically. Bad prompts produce garbage.

### The prompt pattern we use (for every stage)

```
SYSTEM: You are a cybersecurity threat analyst...

TASK: Classify the following darknet post...

INPUT_POST:
---
{raw_text}
---

PRE_EXTRACTED_ENTITIES:
{ner_json}

OUTPUT: Return JSON with exactly these keys:
- threat_type: one of [credential_leak, vulnerability_disclosure, ...]
- severity: integer 1-10
- confidence: float 0.0-1.0
- evidence: list of quoted strings from the post that justify your classification

Do not include explanations outside the JSON.
```

### Why this pattern works
1. **Role priming** ("You are a...") biases the model toward expert vocabulary.
2. **Explicit output schema** with allowed values → no surprises.
3. **Evidence field** forces the model to ground claims in the source text. If it can't quote evidence, the confidence drops naturally.
4. **Pre-extracted entities** save the model work.

### Chain-of-thought? Not here.
Chain-of-thought ("think step by step") helps reasoning but doubles output length and costs time. For extraction/classification, structured output + evidence is better.

### Evidence-based confidence
We don't just trust the LLM's self-reported `confidence`. We **verify** it:
- Do the claimed IOCs actually appear in the text?
- Do the evidence quotes exist in the post (substring check)?
- If half the evidence is hallucinated, we drop confidence ourselves.

---

## 9. The MITRE ATT&CK Framework

### What it is
A **free, public knowledge base** of attacker behavior, maintained by MITRE (a US government-funded nonprofit). Think of it as the "periodic table of cyberattacks."

### Structure (hierarchy)
- **Tactics** (14 total) — the *why*. E.g., "Initial Access," "Execution," "Persistence," "Exfiltration."
- **Techniques** — the *how*. E.g., T1566 "Phishing," T1078 "Valid Accounts."
- **Sub-techniques** — finer-grained. E.g., T1566.001 "Spearphishing Attachment."
- **Procedures** — actual real-world examples of a group using a technique.

Example chain: *Initial Access* → `T1566 Phishing` → `T1566.001 Spearphishing Attachment` → "APT28 sent malicious .docx to Ministry of Defense in 2021."

### Why it's the industry standard
- Used by every SOC, SIEM, EDR, threat intel product on Earth.
- Shared vocabulary — if a report says "T1078," every analyst worldwide knows exactly what's meant.
- Free STIX JSON download: [github.com/mitre/cti](https://github.com/mitre/cti).

### How we use it
When our pipeline analyzes a post, Stage 3 maps described behavior to MITRE technique IDs. Example:

> Post: "I have valid AD creds for the company, you just RDP in."

Maps to: `T1078 Valid Accounts`, `T1021.001 Remote Desktop Protocol`, tactic: *Initial Access / Lateral Movement*.

### The dataset
We download `enterprise-attack.json` (~15 MB) once. It contains every technique with:
- ID (T1078)
- Name ("Valid Accounts")
- Description (long paragraph)
- Kill chain phases (tactics)
- Platforms (Windows, Linux, cloud…)
- References

We'll index technique descriptions for semantic search (next section).

---

## 10. Vector Search / Semantic Retrieval

### The problem
MITRE has ~600 techniques. We can't stuff all of them into an LLM prompt — that's too much text (hits context limits, slow, expensive). We need to **pre-filter to ~10 candidate techniques** per post, then let the LLM pick.

How do we find "the 10 most relevant techniques" given a post?

### Naive approach: keyword search
Search post for technique keywords. Fails for paraphrasing: post says "I can log in as an employee," technique is called "Valid Accounts." No keyword overlap.

### Better approach: semantic search with embeddings
**Embedding** = turn text into a vector (list of ~384 numbers) such that similar meaning → similar vectors. "Valid accounts" and "logged in as employee" end up near each other in vector space.

Workflow:
1. Embed every MITRE technique description once (→ 600 vectors).
2. Embed the post's text.
3. Compute cosine similarity between post vector and each technique vector.
4. Top 10 matches → hand to LLM.

### Tools
- **`sentence-transformers`** library. Model: `all-MiniLM-L6-v2` (80 MB, 384 dims, fast).
- **Vector index:** For 600 items, a plain numpy array + dot product is fine. No FAISS needed. If we scaled to millions, we'd use FAISS.

### One-time indexing
```python
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('all-MiniLM-L6-v2')
technique_vectors = model.encode([t['description'] for t in techniques])  # (600, 384)
np.save('mitre_index.npy', technique_vectors)
```

### Per-query search
```python
post_vec = model.encode(post_text)            # (384,)
scores = technique_vectors @ post_vec         # dot product, (600,)
top10 = scores.argsort()[-10:][::-1]
```

Milliseconds. Then the LLM picks from these 10 and justifies its choices.

---

## 11. Databases: SQLite

### Why SQLite
- **Zero setup.** A single `.db` file. No server, no user accounts, no ports.
- **Embedded.** Lives inside your Python process via the built-in `sqlite3` module.
- **Fast enough.** Handles millions of rows for read-heavy workloads.
- **Perfect for demos.** Entire database is one file you can copy/share.

Alternatives we rejected:
- **Postgres:** overkill, needs a daemon. We'd use it for 100k+ daily posts.
- **MongoDB:** schemaless is a trap for structured intel data.
- **Files (JSON):** no querying, no filtering, pain.

### Our schema (conceptual)

```sql
-- Raw scraped posts
raw_posts (
  id INTEGER PRIMARY KEY,
  source_url TEXT,
  title TEXT,
  author TEXT,
  body TEXT,
  content_hash TEXT UNIQUE,
  scraped_at TIMESTAMP,
  status TEXT         -- 'pending' | 'analyzed' | 'failed'
)

-- NER output
entities (
  id INTEGER PRIMARY KEY,
  post_id INTEGER,
  entity_type TEXT,   -- 'ip' | 'cve' | 'actor' | ...
  value TEXT,
  FOREIGN KEY (post_id) REFERENCES raw_posts(id)
)

-- Full analysis output
analyses (
  id INTEGER PRIMARY KEY,
  post_id INTEGER UNIQUE,
  threat_type TEXT,
  severity INTEGER,
  confidence REAL,
  stage1_json TEXT,   -- raw JSON from each stage
  stage2_json TEXT,
  stage3_json TEXT,
  report_md TEXT,
  created_at TIMESTAMP
)

-- MITRE mappings (joinable, one post → many techniques)
mitre_mappings (
  id INTEGER PRIMARY KEY,
  analysis_id INTEGER,
  technique_id TEXT,   -- 'T1078'
  tactic TEXT,
  confidence REAL,
  reasoning TEXT
)

-- Timeline aggregates
threat_actors (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE,
  first_seen TIMESTAMP,
  last_seen TIMESTAMP,
  post_count INTEGER
)
malware_families (...)  -- same shape
```

### SQLite in Python

```python
import sqlite3
conn = sqlite3.connect('sentinelx.db')
conn.execute('INSERT INTO raw_posts (...) VALUES (?, ?, ?)', (...))
conn.commit()
```

We'll probably use **SQLAlchemy Core** (not the full ORM) for cleaner SQL building, but straight `sqlite3` is fine too.

---

## 12. The Backend API: FastAPI

### What FastAPI is
A modern Python web framework. Key wins over Flask:
- **Async by default** — can handle many concurrent requests.
- **Automatic OpenAPI docs** at `/docs` — free Swagger UI.
- **Pydantic models** — strong typing and validation of request/response bodies.
- **Speed** — one of the fastest Python frameworks.

### Pydantic
Data validation via Python type hints:
```python
from pydantic import BaseModel

class ReportResponse(BaseModel):
    id: int
    threat_type: str
    severity: int
    confidence: float
    report_md: str
    created_at: datetime
```
FastAPI auto-validates incoming JSON, returns 422 on mismatch, and documents the shape in OpenAPI.

### Our endpoint map
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/stats` | Dashboard counts |
| GET | `/api/reports` | List reports (filters: severity, type, date) |
| GET | `/api/reports/{id}` | Single report with full data |
| GET | `/api/reports/{id}/pdf` | WeasyPrint PDF |
| GET | `/api/search?q=` | Full-text search |
| GET | `/api/graph` | Nodes + edges for attack graph |
| GET | `/api/timeline` | Timeline data |
| POST | `/api/scrape/trigger` | Force a scrape run |
| GET | `/api/health` | Liveness check |

### CORS
Browser security blocks `fetch` calls to a different origin by default. Since our React dev server is at `:5173` and API at `:8000`, we enable CORS for specific origins.

### Background workers
FastAPI itself handles requests. The **scraper** and **pipeline worker** run as separate background threads started on app startup. They share the same SQLite DB. Crude but effective for this scale.

If it scales: split into separate services + a queue (Redis/Celery).

---

## 13. The Frontend: React + Vite + Tailwind

### React in 30 seconds
JavaScript library for building UIs as **components** — reusable pieces that render based on their `props` and `state`. Instead of imperatively changing DOM (old jQuery style), you describe "for this data, the UI should look like this," and React handles updates.

```jsx
function SeverityBadge({ score }) {
  const color = score > 7 ? 'red' : score > 4 ? 'yellow' : 'green';
  return <span className={`badge ${color}`}>{score}</span>;
}
```

### Vite
**Build tool + dev server** for modern JS projects.
- Instant start (no webpack bundling on every save).
- Hot Module Replacement — save a file, browser updates the component without reloading.
- Ships production-optimized bundle via Rollup.

Replaces Create React App, which is deprecated.

### Tailwind CSS
**Utility-first CSS framework.** Instead of writing `.severity-badge { color: red; padding: 0.5rem }`, you write `<span className="text-red-500 p-2">`. Classes compose.

Why:
- No naming things.
- Consistent spacing/color scales.
- Tiny final bundle (unused classes are purged).
- Dark theme baked in (`dark:` variants).

### Our page structure
- **`/` Dashboard** — stat cards, recent reports feed.
- **`/reports` Library** — filterable table.
- **`/reports/:id` Detail** — markdown render, IOC table, MITRE matrix, PDF button.
- **`/graph` Attack Graph** — vis.js network fullscreen.
- **`/timeline` Timeline** — horizontal timeline.

### State management
For this size, **React Query** (a.k.a. TanStack Query) is ideal. It handles:
- Fetching data from our API.
- Caching responses.
- Auto-refetching on focus.
- Loading/error states.

No need for Redux.

### Router
`react-router-dom` for URL-based page navigation.

### Markdown rendering
`react-markdown` to turn our report Markdown into styled HTML.

---

## 14. Graph Visualization: vis.js

### The concept
Our attack graph has nodes (actors, malware, CVEs, industries) and edges (relationships: "APT28 uses Emotet," "LockBit targets healthcare"). This is a **network graph** (a.k.a. node-link diagram).

### Why vis.js (vis-network)
- Physics-based layout (nodes repel, edges attract) — looks alive.
- Rich config (colors, shapes, clustering, zoom).
- Works with React via a thin wrapper or a `useEffect`-based mount.
- Handles up to thousands of nodes smoothly.

### Alternatives
- **D3.js:** lower-level, infinite flexibility, steeper curve. Overkill.
- **Cytoscape.js:** also great. Similar power. vis.js is lighter.
- **React Flow:** beautiful but made for diagrams, not exploration graphs.

### Our data shape
```json
{
  "nodes": [
    {"id": "actor:APT28", "label": "APT28", "group": "actor"},
    {"id": "mal:Emotet", "label": "Emotet", "group": "malware"},
    {"id": "cve:CVE-2024-1709", "label": "CVE-2024-1709", "group": "cve"}
  ],
  "edges": [
    {"from": "actor:APT28", "to": "mal:Emotet", "label": "uses"},
    {"from": "mal:Emotet", "to": "cve:CVE-2024-1709", "label": "exploits"}
  ]
}
```

We generate this from SQL: for each analysis, create edges between all entities mentioned. Weight edges by co-occurrence count.

### Interactivity
Click a node → side panel shows all reports mentioning it. Filter by node type. Hover for tooltip.

---

## 15. PDF Generation: WeasyPrint

### Why PDF export matters
Security teams live in PDFs. Reports get shared via email, attached to tickets, forwarded to execs. A "download PDF" button is table-stakes.

### What WeasyPrint is
A **Python library that turns HTML + CSS into PDF**. It implements real print CSS (page size, margins, page numbers, `@page`, `break-before`).

### Why not alternatives
- **ReportLab:** lower-level, you draw boxes by coordinates. Painful.
- **wkhtmltopdf:** uses WebKit, good but abandoned.
- **Puppeteer/headless Chrome:** needs a whole browser. Overkill.
- **pdfkit:** wraps wkhtmltopdf.

WeasyPrint is pure Python (ish — has C deps), actively maintained, best CSS support of the pure-Python options.

### How we use it

```python
from weasyprint import HTML
html_str = render_template('report_pdf.html', report=report_data)
HTML(string=html_str).write_pdf('/tmp/report.pdf')
```

We'll have a dedicated `report_pdf.html` template with:
- Cover page (title, severity, date, company logo placeholder)
- TOC
- Rendered markdown report body
- Footer with confidence % and generation timestamp

### Windows gotcha
WeasyPrint needs GTK libraries on Windows (for font rendering). Alternative for Windows: `pdfkit` + `wkhtmltopdf` or `reportlab`. We'll document this — if WeasyPrint setup on Windows is painful, we'll fall back.

---

## 16. System Design: Full File Structure

```
SentinalX/
│
├── TECHNICAL_PRIMER.md          ← this file
├── README.md                    ← how to set up and run
├── run.ps1                      ← Windows script to start everything
│
├── onion_service/               ← the synthetic .onion forum
│   ├── app.py                   ← Flask app
│   ├── seed_data.py             ← generates 50 realistic posts
│   ├── forum.db                 ← separate SQLite for the forum
│   ├── templates/
│   │   ├── base.html            ← dark theme layout
│   │   ├── index.html           ← thread list
│   │   ├── thread.html          ← single thread
│   │   └── category.html        ← posts by category
│   └── static/
│       └── style.css            ← minimal darknet aesthetic
│
├── tor_config/
│   ├── torrc                    ← Tor daemon config
│   └── hidden_service/          ← Tor writes .onion hostname here
│       └── hostname             ← generated .onion address
│
├── backend/
│   ├── requirements.txt
│   ├── config.py                ← paths, ports, Ollama URL, etc.
│   │
│   ├── db/
│   │   ├── schema.sql           ← DDL for all tables
│   │   ├── init_db.py           ← one-time DB init
│   │   └── models.py            ← Pydantic models + SQL helpers
│   │
│   ├── scraper/
│   │   ├── tor_client.py        ← SOCKS-through-Tor HTTP wrapper
│   │   ├── scraper.py           ← main polling loop
│   │   └── parser.py            ← HTML + JSON post parsers
│   │
│   ├── pipeline/
│   │   ├── ner.py               ← spaCy + regex IOC extractor
│   │   ├── ollama_client.py     ← wraps Ollama HTTP API
│   │   ├── prompts.py           ← all prompt templates
│   │   ├── stage1_classify.py   ← threat classification
│   │   ├── stage2_extract.py    ← intel extraction
│   │   ├── stage3_mitre.py      ← MITRE mapping (with vector search)
│   │   ├── stage4_report.py     ← full report generation
│   │   ├── runner.py            ← orchestrator
│   │   └── verifier.py          ← evidence-grounding checks
│   │
│   ├── mitre/
│   │   ├── loader.py            ← downloads MITRE JSON
│   │   ├── indexer.py           ← builds embedding index
│   │   └── search.py            ← top-K retrieval
│   │
│   ├── api/
│   │   ├── main.py              ← FastAPI app entrypoint
│   │   ├── deps.py              ← DB session, auth, etc.
│   │   ├── routers/
│   │   │   ├── reports.py
│   │   │   ├── graph.py
│   │   │   ├── timeline.py
│   │   │   ├── search.py
│   │   │   └── stats.py
│   │   ├── pdf/
│   │   │   ├── generator.py     ← WeasyPrint wrapper
│   │   │   └── report_pdf.html  ← PDF template
│   │   └── workers.py           ← background scraper + pipeline threads
│   │
│   └── tests/
│       ├── test_ner.py
│       ├── test_pipeline.py
│       └── test_api.py
│
├── data/
│   ├── mitre/
│   │   ├── enterprise-attack.json   ← MITRE data dump
│   │   └── mitre_index.npy          ← pre-computed embeddings
│   └── models/                      ← spaCy model cache
│
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   ├── tailwind.config.js
│   ├── index.html
│   └── src/
│       ├── main.jsx                 ← React entrypoint
│       ├── App.jsx                  ← router setup
│       ├── api/
│       │   └── client.js            ← axios/fetch wrapper
│       ├── pages/
│       │   ├── Dashboard.jsx
│       │   ├── ReportLibrary.jsx
│       │   ├── ReportDetail.jsx
│       │   ├── AttackGraph.jsx
│       │   └── Timeline.jsx
│       ├── components/
│       │   ├── Navbar.jsx
│       │   ├── SeverityBadge.jsx
│       │   ├── IOCTable.jsx
│       │   ├── MitreMatrix.jsx
│       │   ├── ConfidenceBar.jsx
│       │   └── GraphCanvas.jsx     ← vis.js wrapper
│       ├── hooks/
│       │   ├── useReports.js
│       │   └── useGraph.js
│       └── utils/
│           └── format.js
│
└── sentinelx.db                ← main SQLite database
```

---

## 17. Data Flow: A Single Post's Journey

Step-by-step, from "someone posts on the forum" to "report shows in React":

1. **T+0s** — Fake user seed script creates post in `onion_service/forum.db`.
2. **T+~5s** — Scraper polls `http://xyz.onion/api/posts?since=...` via Tor SOCKS proxy.
3. **T+~10s** — Tor circuit established; response comes back; scraper parses JSON.
4. **T+~11s** — SHA-256 hash computed; `INSERT OR IGNORE` into `raw_posts`. Status = `pending`.
5. **T+~12s** — Pipeline worker picks up `pending` row.
6. **T+~12.1s** — spaCy NER + regex → entities extracted → stored in `entities`.
7. **T+~15s** — Stage 1: Ollama `/api/generate` classifies threat. JSON stored.
8. **T+~20s** — Stage 2: Ollama extracts detailed intel.
9. **T+~21s** — Sentence-transformer embeds post → top-10 MITRE techniques retrieved.
10. **T+~25s** — Stage 3: Ollama picks + justifies → `mitre_mappings` populated.
11. **T+~35s** — Stage 4: Ollama writes markdown report.
12. **T+~36s** — Verifier checks evidence grounds; final confidence computed.
13. **T+~36.5s** — `analyses` row written. Status → `analyzed`.
14. **T+~40s** — Frontend (React Query polling `/api/reports` every 10s) sees new row.
15. **T+~40.1s** — User clicks report → `/api/reports/{id}` → renders markdown + IOC table + MITRE matrix.
16. **T+~41s** — User clicks "Download PDF" → WeasyPrint renders → file streamed to browser.
17. **T+~45s** — User opens Attack Graph → `/api/graph` queries joins across `entities` + `analyses` → vis.js renders network.

Total: ~45 seconds from post to actionable report. **Compared to ~4 hours manually.**

---

## 18. Glossary of Every Term

| Term | Meaning |
|---|---|
| **API** | Application Programming Interface. Contract for how programs talk to each other. |
| **APT** | Advanced Persistent Threat. Usually a nation-state hacking group. |
| **BTC wallet** | Bitcoin address. Often used for ransomware payments. |
| **CORS** | Cross-Origin Resource Sharing. Browser security rule for cross-domain requests. |
| **CTI** | Cyber Threat Intelligence. |
| **CVE** | Common Vulnerabilities and Exposures. Public vulnerability ID. |
| **DDoS** | Distributed Denial of Service — flooding a target to knock it offline. |
| **EDR** | Endpoint Detection and Response. Security tool on laptops/servers. |
| **FAISS** | Facebook's vector similarity search library. |
| **FastAPI** | Python async web framework. |
| **FUD** | "Fully UnDetectable" — malware that evades antivirus. |
| **GPE** | Geo-Political Entity. spaCy tag for countries/cities. |
| **Hidden service** | Tor site whose server IP is also anonymous. `.onion` address. |
| **IOC** | Indicator of Compromise. Concrete artifact of an attack. |
| **Jinja2** | Python templating engine used by Flask. |
| **Jitter** | Random variation in timing, to avoid detection as a bot. |
| **LLM** | Large Language Model. |
| **MITRE ATT&CK** | Framework of attacker tactics and techniques. |
| **NER** | Named Entity Recognition. |
| **Ollama** | Local LLM runtime. |
| **ORG** | Organization. spaCy entity tag. |
| **PII** | Personally Identifiable Information. |
| **Pydantic** | Python data validation via type hints. |
| **Quantization** | Compressing LLM weights to smaller bit-widths. |
| **React** | JavaScript UI library. |
| **RDP** | Remote Desktop Protocol. Windows remote login. Huge attack vector. |
| **Relay** | A Tor server that forwards encrypted traffic. |
| **Rendezvous point** | Tor relay where client and hidden service meet. |
| **SIEM** | Security Information and Event Management. Log aggregation + alerts. |
| **SOC** | Security Operations Center. Humans watching alerts. |
| **SOCKS5** | Generic proxy protocol. Tor exposes one on 9050. |
| **spaCy** | Python NLP library. |
| **STIX** | Structured Threat Information eXpression. CTI data format. |
| **Stem** | Python library to control Tor. |
| **Tailwind** | Utility-first CSS framework. |
| **TTP** | Tactics, Techniques, Procedures. |
| **Temperature** | LLM randomness parameter. |
| **Tor** | The Onion Router anonymity network. |
| **Tor daemon** | Background Tor process. |
| **Vector embedding** | Numeric representation of meaning. |
| **vis.js** | JavaScript graph visualization library. |
| **Vite** | Frontend build tool. |
| **WeasyPrint** | Python HTML-to-PDF renderer. |
| **WSL** | Windows Subsystem for Linux. |

---

## What to Read Next

Now that you've got the full picture:
1. Skim sections 3 (Tor) and 7 (LLMs) again — those are the two most important concepts for the "wow" factor of this project.
2. Look at section 17 (data flow) — that's the narrative you'll use in any demo or interview.
3. Glossary (18) — bookmark for when a term pops up and you forget.

When you're ready, we move to **Stage 1: building the synthetic .onion forum.**
