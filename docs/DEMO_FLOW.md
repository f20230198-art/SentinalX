# SentinelX — Live Demo Flow

> A step-by-step run sheet for demoing SentinelX after the pitch slides.
> This is a **flow**, not a word-for-word script — it tells you what to open,
> what to click, and what to say *about* each thing. Speak naturally.
>
> **Total time: ~5–6 minutes** (3 min if you skip the optional parts).
> **Audience assumption:** they just saw your 6-slide deck. They know *what*
> SentinelX is. The demo proves it's **real** — not slides, not mockups.

---

## 0. Before you walk in — pre-flight checklist

Do all of this **before** the demo. None of it is shown to the audience.

### 0.1 Plug in the laptop
The LLM (Mistral) needs the GPU. On battery it throttles to 25W and is slow.
**Plug in.** If you genuinely can't, you'll use FAST MODE later (see §5b).

### 0.2 Start everything (in order)

Open a terminal in the project folder. Run these, each in its own terminal
tab/window, and leave them running:

```powershell
# 1. Docker containers — both .onion forums + their Tor
docker compose up -d

# 2. Backend API (port 8765)
backend/.venv/Scripts/python.exe -m uvicorn backend.api.main:app --port 8765

# 3. Frontend (port 5173)
cd frontend
npm run dev
```

Also make sure **Ollama** is running with Mistral pulled:
```powershell
ollama list        # should show mistral
```

### 0.3 Verify everything is up

- Backend: open <http://localhost:8765/healthz> → should say `{"status":"ok"}`
- Frontend: open <http://localhost:5173> → the dashboard loads
- The header has a **watch indicator** pill — it should show db / Ollama / Tor
  all green. If Tor or Ollama is red, fix that before the demo.

### 0.4 Pre-ingest SilkVault (CRITICAL — do this early)

SentinelX should already have **both forums** loaded:
- DarkBay  — 236 posts
- SilkVault — 188 posts

If SilkVault is missing, ingest it now (this takes ~10 min — **do it well
before the demo**, never live):
1. Open <http://localhost:5173/scout>
2. Paste SilkVault's .onion address (get it with
   `cat tor_config_silkvault/hidden_service/hostname`)
3. Hit **RUN PIPELINE**, leave it. Come back when it says `done`.

### 0.5 Open these tabs ahead of time

Have these ready so you're not typing URLs live:
- **Tab 1:** <http://localhost:5173> — the SentinelX dashboard
- **Tab 2:** Tor Browser, on SilkVault's .onion address
- Terminal windows minimised but reachable

### 0.6 Know your two addresses

Write these on a sticky note — you'll need SilkVault's during the demo:
- DarkBay:   `ryntxkxpk6zzeo6gtirtjwadt7hcfgd6lfmqqtut5b6l5qv7imsdapyd.onion`
- SilkVault: `zfb24qalqnphyo673b3spap7w34umoijgnb7ycqcuntoawqthn2rxdad.onion`

> Note: these addresses are generated on *your* machine. If you ever rebuild
> from scratch they change — re-check the files above.

---

## The demo — 6 acts

Each act has **DO** (what you click) and **SAY** (the point to make — your
words, not a script).

---

### ACT 1 — The source: a real darknet forum  (~45 sec)

**DO:**
- Switch to **Tor Browser** (Tab 2), already on SilkVault.
- Scroll the forum — listings, vendors, boards.

**SAY:**
- "This is a darknet forum, running as a real Tor hidden service — a real
  `.onion` address, reached over a real Tor circuit."
- "It's synthetic — we host it ourselves so the demo is legal and reliable —
  but the scraper hits it exactly the way it would hit a real darknet forum."
- "This is where threat actors *announce* attacks first: stolen credentials,
  fresh exploits, network access for sale. The warning signs are all here —
  they're just buried in noise."

> Why this act: grounds everything. They see the raw, ugly source material
> before you show the polished output.

---

### ACT 2 — The dashboard: noise turned into intelligence  (~1 min)

**DO:**
- Switch to the **SentinelX dashboard** (Tab 1), on the home / console page.
- Click into **[02] FEED** — the live post timeline.
- Click any one post to open its detail panel on the right.

**SAY:**
- "SentinelX scraped that forum and ran every post through a pipeline. Here's
  the result."
- (On a post detail) "For each post it pulled out the **IOCs** —
  indicators of compromise: IP addresses, domains, file hashes, CVEs, crypto
  wallets — automatically."
- "It wrote a plain-English **summary** of the threat using a local AI model
  — running on this laptop, no cloud, no API cost."
- "And it mapped the post to **MITRE ATT&CK** — the industry-standard
  catalogue of attacker techniques."
- Point at the **Defensive Recommendations** card: "And critically — it
  doesn't just say *what the threat is*, it says *what to do about it*.
  These are MITRE's official countermeasures for those techniques."

> Why this act: this is the core value. "Hours of manual reading → seconds,
> and it tells you the fix."

---

### ACT 3 — Explainability: it shows its work  (~45 sec)

**DO:**
- Still on a post detail — point at the technique tags and the IOC list.
- Go to **[03] MITRE** — the ATT&CK heatmap. Click a hot cell.

**SAY:**
- "Nothing here is a black box. Every IOC traces back to a regex match. Every
  technique is either something the AI claimed *and we verified* against the
  real MITRE corpus, or something a similarity search discovered. Every tag
  tells you which."
- (On the heatmap) "This is every attacker tactic, across the whole corpus —
  you can see at a glance what this forum is mostly about. Click any cell and
  it drills down to the exact posts."

> Why this act: trust. Analysts won't use a tool that can't justify its
> claims. This separates SentinelX from "ask ChatGPT to summarise it."

---

### ACT 4 — Investigations + reports  (~45 sec) — *optional, skip if short on time*

**DO:**
- Go to **[04] INVESTIGATIONS**. Open one.
- Point at the lens summary with the `[#NNN]` citation links.
- Click **EXPORT PDF** — show the generated report.

**SAY:**
- "An investigation is a saved lens over the corpus — the AI fuses many posts
  into one narrative, and every claim is footnoted back to the source post."
- "And it exports as a styled PDF report — cover page, MITRE coverage chart,
  recommended actions. This is the deliverable a real CTI team hands up the
  chain."

> Why this act: shows it's a finished product, not a tech demo. Cut this if
> you're tight on time — Acts 5 + 6 matter more.

---

### ACT 5 — The live proof: point it at a NEW forum  (~1.5 min)

This is the moment. Everything so far could be pre-baked. Now you prove it
works **live, on something it has never seen.**

**DO:**
1. Switch to **Tor Browser** (SilkVault). Click **"+ post listing"** in the
   sidebar.
2. Fill in the form — make it realistic and IOC-rich. Example:
   - **Title:** `[SELLING] RDP access — EU manufacturing firm`
   - **Board:** `network-access`
   - **Vendor:** `your_handle`
   - **Body:** *"fresh domain admin access to an EU manufacturing firm. entry
     via CVE-2024-21762 on their VPN gateway. C2 staged at 91.219.236.18.
     comes with an AsyncRAT loader for persistence. escrow only, payment
     bc1qe7m2p9k4r6t8w1n3x5z7c0v2b4n6m8q0w2e4r5."*
3. Submit — you're now looking at your new listing on the forum.
4. Switch to the **SentinelX dashboard → [05] SCOUT**.
5. Paste **SilkVault's .onion address** into the bar.
6. Hit **RUN PIPELINE**.
7. Watch the live progress checklist: **scrape → extract → LLM → MITRE**.

**SAY:**
- (While posting on the forum) "I'm posting a brand-new threat to the forum,
  right now — like a real threat actor would."
- (Pasting the URL into Scout) "SentinelX has never processed *this* post.
  I'm just giving it the forum's address."
- (Watching progress) "It's crawling the forum over Tor… it parses the HTML —
  this forum has no API, just like a real one… extracting IOCs… enriching
  with the AI… mapping to MITRE."
- (When done) "About a minute. It only processed the one new post — it
  remembers what it's already seen."
- Go to **[02] FEED**, open the new post: "There it is — my post, fully
  enriched. IOCs pulled, technique mapped, defensive recommendation attached.
  Live, end to end."

> Why this act: this is the whole pitch in 90 seconds. It's not a one-forum
> demo — paste *any* forum's address and it works. **This is the act that
> wins it.**

---

### ACT 5b — If you're on battery (FAST MODE fallback)

If you couldn't plug in, the LLM step is too slow to watch. Do Act 5 exactly
the same, but:
- **Before hitting RUN PIPELINE**, tick the **"FAST MODE — skip LLM
  enrichment"** checkbox.
- **SAY:** "I'll run it in fast mode — it skips the AI summary step but still
  scrapes, extracts every IOC, and maps it to MITRE ATT&CK. The technique
  mapping uses a similarity search, not the big model, so it's fast."
- The post still appears with IOCs and techniques — just no prose summary.
- **SAY:** "On a proper machine the AI summary fills in too — but even the
  fast path gives you the IOCs and the ATT&CK mapping in seconds."

---

### ACT 6 — Close  (~30 sec)

**DO:**
- Back to the dashboard home, or the heatmap — something that looks complete.

**SAY:**
- "Everything you just saw runs on **one laptop**, with **zero paid APIs** —
  Tor, a local AI model, open-source libraries, and MITRE's public data."
- "It takes hours of manual darknet monitoring and compresses it to seconds —
  and unlike just asking an AI, **every single claim is traceable.**"
- "And it's not locked to our forum. Point it at a new one, and it works."
- "That's SentinelX — explainable, actionable darknet threat intelligence."

---

## Quick recovery — if something breaks mid-demo

Stay calm, these are all fast fixes:

| Problem | Fix |
|---|---|
| Scout job stuck on "scraping" a long time | Normal — the Tor crawl takes ~90s. Keep talking. |
| Job shows `error` | The forum/Tor may have hiccuped. Just hit RUN PIPELINE again — jobs are safe to re-run, it resumes. |
| Watch indicator shows Ollama red | Ollama stopped. The job still works in FAST MODE — tick the box. |
| Dashboard won't load | Backend died. Restart it (terminal 2 command in §0.2). |
| Tor Browser can't reach SilkVault | `docker compose restart silkvault tor-sv`, wait 30s. |
| Whole thing feels slow | You're probably on battery. Plug in, or use FAST MODE. |

**Golden rule:** if the live scrape misbehaves, you still have Acts 1–4 —
the pre-loaded 188 SilkVault + 236 DarkBay posts are real and already there.
The live act is the showpiece, not the only proof.

---

## One-line summary of the flow

> Show the forum (raw) → show the dashboard (enriched) → show it's
> explainable → *(optional: investigations + PDF)* → **post a new threat and
> scrape it live** → close on "free, local, explainable, works on any forum."
