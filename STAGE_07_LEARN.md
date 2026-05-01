# Stage 7 — React/Vite/Tailwind frontend (deep dive)

> Read first: `CLAUDE.md` §3 for the project map, `STAGE_06_LEARN.md` for the FastAPI surface this frontend consumes, `STAGE_07_DESIGN.md` for the visual identity decisions made before code was written.
>
> This doc covers everything that landed across Sessions A, B, C, and the post-C polish pass. It explains *what* exists, *how* the pieces work, *why* each choice over alternatives, and *where* the patterns show up in industry CTI tooling.

---

## 0. What this stage is, in one paragraph

Stage 7 is the frontend that turns SentinelX from a pipeline of CLI commands into something a human can actually *use*. It ships five routes — a console (`/`), a vertical-timecord live feed (`/posts`), a MITRE ATT&CK heatmap (`/techniques`), an investigations workspace with LLM-generated lens summaries (`/investigations`), and an IOC pivot graph (`/iocs/:value`) — all tied together by a shared post-detail panel, a header watch-indicator, and a server-sent event stream that animates new intel into the timeline as the pipeline produces it. Visually it leans on a WebGL violet plasma shader, a 5-phase boot sequence, and a *Dark*-inspired vertical timecord.

---

## 1. Stack & why each piece

| Layer                       | Choice                                                | Why                                                                                                                                                         |
|-----------------------------|-------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Build/dev tool              | **Vite 6**                                            | Sub-second cold start; the `/api` proxy keeps everything same-origin in dev so we don't pay CORS preflights on every fetch.                                |
| UI framework                | **React 18 + TypeScript**                             | Industry default for dashboards. Strict typing keeps the API contract honest as the FastAPI surface evolves.                                               |
| Styling                     | **Tailwind v4 (beta) with `@theme` tokens**           | Design tokens live in CSS, not JS — `--color-accent` works in inline styles, SVG, anywhere. v4 is post-PostCSS so config is just `@import "tailwindcss"`.   |
| Routing                     | **react-router-dom v6**                               | Five routes; v6's `<Routes>`/`<Route>` API is the smallest thing that works.                                                                               |
| Server state                | **TanStack Query v5**                                 | Built-in caching, dedup, refetch-on-focus, polling. The watch indicator's 12s poll is one line: `refetchInterval: 12000`. No Redux for stuff the server owns. |
| Animations                  | **Framer Motion**                                     | Declarative entry/exit animations, `AnimatePresence` for the side panel slide-in. We considered GSAP for the boot but rejected it (see §11).               |
| Background                  | **Three.js**                                          | One full-viewport plane running a custom GLSL fragment shader. Three is overkill for one shader, but it manages the WebGL context, RAF loop, and DPR for us.|
| Live updates                | **EventSource (SSE)**                                 | One-way server-push, no socket protocol upgrade, survives proxies, auto-reconnects. Right tool when the server has nothing to receive from the client.     |
| Force layout                | **d3-force** (IOC pivot graph only)                   | Battle-tested, headless (we draw the SVG ourselves), small (`~40 KB`), no React-renderer coupling.                                                          |

Things we deliberately did *not* use:
- **No Redux/Zustand/Jotai.** All shared state is either URL state (router), server state (Query), or component state. Adding a global store for ~5 routes is overkill and a recipe for stale data.
- **No CSS-in-JS** (styled-components, emotion). Tailwind tokens cover it; runtime CSS-in-JS would slow first paint.
- **No charting library** (recharts, visx). The heatmap, top-techniques bar, and pivot graph are all hand-drawn SVG/divs — nothing complex enough to justify the dep weight.
- **No Storybook** at this scale. The component count is small enough to navigate by eye.

---

## 2. Repo layout

```
frontend/
├── index.html                ← Vite entry. <div id="root"> + nothing else.
├── vite.config.ts            ← /api proxy → http://127.0.0.1:8765
├── package.json              ← deps; d3-force kept for /iocs/:value
├── tsconfig.json             ← strict mode on
└── src/
    ├── main.tsx              ← <BrowserRouter><QueryClientProvider><App /></...></BrowserRouter>
    ├── App.tsx               ← Boot gate + 5 <Route>s + ShaderBackground
    ├── index.css             ← Tailwind v4 import + @theme tokens + global rules
    │
    ├── components/
    │   ├── Shell.tsx         ← Header (logo + nav + WatchIndicator + CRT toggle), SectionDivider
    │   ├── ShaderBackground.tsx  ← three.js full-viewport violet plasma
    │   ├── BootSequence.tsx  ← 5-phase entry animation
    │   ├── DetailPanel.tsx   ← Shared right-side post drawer (used by 4 pages)
    │   ├── CitationText.tsx  ← Splits text on [#NNN], renders each as a button
    │   └── WatchIndicator.tsx ← Header status pill polling /healthz/full
    │
    ├── pages/
    │   ├── Home.tsx          ← Hero + Health grid + FeedTeaser + TopTechniques
    │   ├── Timeline.tsx      ← Vertical timecord (Dark-inspired)
    │   ├── Heatmap.tsx       ← MITRE ATT&CK 7+7 tactic matrix
    │   ├── Investigations.tsx ← List + detail with citations
    │   └── IocPivot.tsx      ← d3-force pivot graph
    │
    ├── hooks/
    │   ├── useCrtMode.ts     ← Toggles body[data-crt="on"] + sessionStorage
    │   ├── useCursorHalo.ts  ← Tracks cursor; updates --mx/--my CSS vars
    │   ├── useScramble.ts    ← Type-scramble effect for hero text
    │   └── useLivePosts.ts   ← Live-only EventSource subscription
    │
    └── lib/
        └── api.ts            ← Tiny typed wrapper around fetch(/api/...)
```

---

## 3. The visual identity (Sessions A + the polish pass)

### 3.1 Tokens and contrast (`index.css`)

```css
@theme {
  --color-base: #0a0612;
  --color-deep: #13033b;
  --color-surface-1: #130927;
  --color-surface-2: #1c1035;
  --color-border-soft: #2a1f45;
  --color-text: #f4f2fb;        /* lifted in polish pass for legibility */
  --color-text-muted: #a8a0c4;  /* lifted in polish pass for legibility */
  --color-accent: #a78bfa;
  --color-warn: #e8a33d;
  --color-danger: #e5484d;
}
```

**Why CSS variables, not Tailwind theme config:** Tailwind v4 reads `@theme` and emits utility classes (`bg-base`, `text-accent`, etc.), but the *raw variables* are still available in CSS, inline styles, and SVG `fill=`/`stroke=` attributes. That's a single source of truth across HTML, CSS, and JS — change one number and the whole UI follows.

**Heading reset (post-polish fix):** Tailwind v4 ships *no* heading reset; `h1`–`h6` inherit from `body`, but Chrome's UA stylesheet defines `h3 { color: -webkit-link }` for some contexts and we hit a case where the FeedTeaser thread titles rendered as invisible black text on the violet background. The fix is one rule:

```css
h1, h2, h3, h4, h5, h6 { color: var(--color-text); }
```

Industry parallel: every dashboard built on a dark theme eventually re-discovers this. Stripe Dashboard, Linear, and Vercel all ship explicit heading colors for the same reason.

### 3.2 The grain + dim veil

Two pseudo-elements on the `.grain` root:

- `::before` is a fixed-position SVG noise pattern at 4% opacity, `mix-blend-mode: screen`. Reads as faint dust on the screen rather than visual noise.
- `::after` is a radial gradient `rgba(10,6,18, 0.55→0.92)` sitting between the WebGL shader and the page content. Without it the violet plasma fights long-form text. With it, the shader is visible at the edges (still atmospheric) and content sits on a darker substrate.

Both are inline data URIs — no extra HTTP request, no CORS. Total weight: a few hundred bytes.

### 3.3 The WebGL background (`ShaderBackground.tsx`)

A single `THREE.PlaneGeometry` (full-viewport quad) with a custom `ShaderMaterial`. The fragment shader runs **fbm domain-warped noise**:

1. Sample 2D simplex/value noise.
2. Use the noise to perturb the input coordinates (the "domain warp").
3. Sample again. Repeat 2–3 octaves.

The result is the swirling violet plasma. Two interactions:
- **Cursor**: a uniform vector pulls the flow toward the cursor + adds a soft halo.
- **Click**: a uniform timestamp triggers an expanding shockwave ring.

Performance guards:
- `pixelRatio` capped at 1.5x DPR (high-DPI displays would otherwise burn GPU).
- `requestAnimationFrame` paused on `visibilitychange` so the shader doesn't run when the tab is backgrounded.

**Where this shows up in industry:** GPU-rendered backgrounds are everywhere now — Stripe homepage, Linear, Vercel marketing pages, OpenAI's site. The pattern is the same: a single full-viewport plane + a fragment shader doing all the work in parallel on the GPU. CPU stays free for the rest of the app.

### 3.4 The boot sequence (`BootSequence.tsx`)

5 phases in 2.5s total:

1. **Static** — TV-noise overlay (animated SVG turbulence, 4-step shifts at 120ms).
2. **Iris-open** — circular mask expands from center.
3. **World-map with hubs** — 8 pinging .onion hub nodes on a faint world graphic.
4. **Drips** — 7 violet drips fall from the top.
5. **Glitch cut** — chromatic-aberration flash, then handoff.

**Critical implementation detail — single-fire under React StrictMode:** in dev, React intentionally double-mounts every component to surface effect-cleanup bugs. Refs and `useEffect` cleanup don't survive the unmount. The fix is module-scope flags:

```tsx
let bootStarted = false;
let bootDone = false;

export function BootSequence({ onDone }: { onDone: () => void }) {
  // ... reads/writes bootStarted, bootDone at module scope
}
```

Module variables persist across mounts (they're per-module, not per-component instance). Combined with `sessionStorage` for the skip-on-revisit behaviour, the boot fires exactly once per browser tab.

**Why we did *not* port to GSAP:** evaluated when the user asked. Verdict: GSAP would add ~30–40 KB gzipped to the boot bundle (the one place users wait for paint), introduce a second animation system alongside Framer Motion (already in bundle), and offer marginal visual gain. We added spine-shimmer + comet-trail effects on the timeline instead — same energy budget, applied to something users see *every session*.

### 3.5 Cursor halo + CRT mode

Two opt-in flourishes:

- `useCursorHalo()` updates `--mx` and `--my` CSS vars on `mousemove`. A `.cursor-halo::before` pseudo-element renders a soft violet glow at that position. Pure CSS, no React re-renders.
- `useCrtMode()` toggles `body[data-crt="on"]`. CSS picks it up: a fixed-position `::after` paints scanlines (`repeating-linear-gradient`) and the body gets a faint violet text-shadow. Persists in `sessionStorage`.

**Industry parallel:** "presentation mode" toggles in real CTI tools (CrowdStrike Falcon, Recorded Future) usually do something similar — change density, hide chrome — for war-room screens.

---

## 4. Data layer (`lib/api.ts`)

A 50-line typed fetch wrapper:

```ts
const BASE = "/api";

async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const qs = params ? "?" + new URLSearchParams(...).toString() : "";
  const r = await fetch(`${BASE}${path}${qs}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on GET ${path}`);
  return r.json() as Promise<T>;
}
```

**Why a hand-rolled wrapper instead of an OpenAPI codegen client:** the FastAPI surface is small (~15 endpoints) and intentionally frozen mid-stage. Codegen is worth its complexity at ~100+ endpoints or when the backend evolves faster than the frontend; we're neither. The trade is: types are duplicated, so when the backend adds a field we have to type it here too. Cheap, explicit.

The `api` object is just a record of methods:

```ts
export const api = {
  healthz: () => get<{status: string}>("/healthz"),
  healthzFull: () => get<HealthFull>("/healthz/full"),
  stats: () => get<Stats>("/stats"),
  posts: (params?) => get<PostList>("/posts", params),
  post: (id: number) => get<PostDetail>(`/posts/${id}`),
  techniques: (params?) => get<TechniqueList>("/techniques", params),
  technique: (id: string) => get<TechniqueDetail>(`/techniques/${id}`),
  iocs: (params?) => get<IocList>("/iocs", params),
  lenses: () => get<{items: Lens[]}>("/lenses"),
  investigations: () => get<{items: Investigation[]}>("/investigations"),
  investigation: (id: number) => get<Investigation>(`/investigations/${id}`),
  createInvestigation: (body) => post<Investigation>("/investigations", body),
  rerun: (id: number) => post<Investigation>(`/investigations/${id}/rerun`, {}),
  deleteInvestigation: async (id: number) => { /* DELETE 204 */ },
};
```

Every call goes through TanStack Query at the call site:

```ts
const stats = useQuery({ queryKey: ["stats"], queryFn: api.stats });
```

This is what gives us free caching, dedup (two components asking for the same key → one HTTP), and refetch policies.

---

## 5. The vertical timecord (`pages/Timeline.tsx`)

This is the centerpiece, and it was rewritten twice — first as a horizontal beeswarm (Session B), then as the *Dark*-inspired vertical timecord (polish pass) after the user said the horizontal layout "didn't read as a timeline."

### 5.1 Layout

A single absolutely-positioned `div` runs top→bottom at `left: 50%`, styled as a vertical violet gradient with an outer glow. Day groups stack down the page; within each day, posts branch alternately right ↔ left as content cards.

```
                      ●  ← spine head pulse
                      │
              ┌───────┤
              │       │   28 APR 2026 · 12   ← day pill on spine
              └───────┤
                      │
                      ●─────── card ───────┐
                      │  #234 SALE          │
                      │  Cisco IOS XE 0day  │
                      │  T1190 T1078 T1566  │
                      └─────────────────────┘
                      │
   ┌───────── card ───●
   │  #233 DOXXING    │
   └──────────────────┘
                      │
```

Day groups: newest day on top. Within a day: newest post on top. So the page reads top-down like a feed, with the spine giving you the time axis at a glance.

### 5.2 SSE bootstrap → live handoff

Two-phase data load:

1. **Bootstrap (replay)**: `EventSource("/api/events?since_id=0")` — the backend treats `since_id=0` as "replay everything." A debounced settle timer flips `historyDone = true` after 2.5s of silence.
2. **Live**: once history is done, `useLivePosts(maxIdSoFar)` opens a *new* EventSource at `since_id=<latest>` for live-only pushes. Bootstrap connection is closed.

Why two connections instead of one: the bootstrap connection ends the moment we're done with it, freeing one of the browser's six per-origin HTTP/1.1 connection slots. The live connection is the only long-lived one.

**Bug we fixed in Session B (worth remembering):** the backend's original SSE handler used `last = since_id or MAX(id)`. Python's truthiness rule says `0 or X == X`, so a bootstrap client sending `since_id=0` got *nothing* (it got `MAX(id)+1` onwards). The fix is one line: `last = since_id` (literal). Always test the boundary.

### 5.3 Live arrival animation

Two visual cues fire when `arrivingIds` is non-empty:

- **Spine shimmer** — a bright violet pulse races top→bottom along the spine over 1.6s (Framer Motion, `top: 0` → `top: 100%`).
- **Comet trail** — under the pulsing node itself, a fading violet line scales down over 4s. Reads like the node "dropped" energy into the spine.

Both are scoped to `arriving === true` on the relevant nodes; auto-cleanup via `setTimeout` after 4.5s.

**Why these, not GSAP:** see §3.4. The effects are 30 lines of Framer Motion, no new deps.

### 5.4 Filters

Seven filter chips at the top: `ALL · SALE · DISCUSSION · DOXXING · RECRUITMENT · WITH CVE · WITH BTC`. Non-matching branches dim to 18% opacity rather than disappearing — keeps the time spine intact even when filtering.

**Industry parallel:** This is the "facet sidebar" pattern in every CTI dashboard (Splunk, Recorded Future, MISP) — except inline with the data instead of off to one side, because at this scale the filter set is small enough.

### 5.5 Hover for techniques

Each branch card already lists its first 6 MITRE T-codes as colored chips. Hovering the branch lifts the card border + node glow. Click → `DetailPanel` slides in.

### 5.6 The DetailPanel (`components/DetailPanel.tsx`)

The right-side drawer — initially inline in `Timeline.tsx`, extracted in Session C so `Heatmap`, `Investigations`, and `IocPivot` could share it.

It calls `api.post(id)` via Query (cached: re-opening the same post is instant), and renders LLM summary, body, MITRE techniques (color-coded by source: violet=verified, cyan=semantic, amber=unverified), IOCs, and entities. **IOC chips are now `<Link>`s to `/iocs/:value`** — one click pivots from a single post into every other post mentioning that IOC.

---

## 6. The MITRE heatmap (`pages/Heatmap.tsx`)

The classic ATT&CK Navigator look, our way:

- 14 Enterprise tactics, split into **two rows of seven**: *Pre-compromise → Foothold* (Recon, Resource Dev, Initial Access, Execution, Persistence, Priv Esc, Defense Evasion) and *Operate → Objective* (Cred Access, Discovery, Lateral Move, Collection, C2, Exfiltration, Impact). One row of 14 felt cramped; user asked for the split.
- Each tactic column lists every technique in our corpus that touches it (`only_seen=true`), sorted by `post_count` desc.
- Cells shaded by `post_count` on a **log scale**:
  ```ts
  const t = Math.min(1, Math.log(1 + count) / Math.log(1 + max));
  const alpha = 0.12 + t * 0.78;  // 0.12–0.90 violet
  ```
  Log scale because one hot technique (T1566 Phishing, 153 posts) would otherwise wash out everything else.
- Click a cell → side panel with the technique's description + the list of posts mapped to it. Click a post → shared `DetailPanel`.

**Why this is defensible:** real CTI tools — MITRE Navigator itself, CrowdStrike Falcon's threat overview, Recorded Future's threat actor profiles — all show this view. It's the single most recognizable shape in CTI dashboards. Visually striking, demo-ready, and it earns its keep because it's the only view that shows the *spread* of behaviours across the kill chain at a glance.

**Multi-tactic technique handling:** a technique can belong to multiple tactics (e.g. T1078 Valid Accounts is in Initial Access *and* Persistence *and* Privilege Escalation *and* Defense Evasion). We render it in every relevant column. The sort order keeps it consistent.

---

## 7. Investigations + lens citations (`pages/Investigations.tsx`, `components/CitationText.tsx`)

This is where the LLM lens summaries from Stage 6.5 get a UI.

### 7.1 Layout

- **Left (4 cols)**: list of saved investigations, each row showing `#id NAME · LENS`.
- **Right (8 cols)**: detail of the selected investigation — name, description, lens, filter chips, matched-post count, lens summary, and a list of matched posts.
- A `[+ NEW]` button drops a create form: name input, lens picker (chips), intent + keyword filters, `[ CREATE ]`.

### 7.2 Citation rendering

The lens summaries from Stage 6.5 emit citations as `[#210]` inline. The `CitationText` component renders them as clickable buttons:

```ts
const CITE = /\[#(\d+)\]/g;

export function CitationText({ text, onSelect }) {
  const parts = [];
  let last = 0;
  for (const m of text.matchAll(CITE)) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    parts.push({ id: Number(m[1]), raw: m[0] });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts.map(p =>
    typeof p === "string"
      ? <span>{p}</span>
      : <button onClick={() => onSelect(p.id)}>#{p.id}</button>
  )}</>;
}
```

Click → `DetailPanel` opens for that post. The `summary_post_ids` array on the investigation also gets clickable rows below the summary.

**Why this matters:** lens summaries are LLM output, so analysts have to be able to verify every claim against a source post. The `[#NNN]` citation format makes the LLM "show its work." Rendering each one as a clickable link closes the loop — read claim → tap → see source.

**Industry parallel:** Notion AI, Perplexity, Google's Gemini all do citation-as-link on RAG answers. CTI tools (Recorded Future Intelligence Cards) have done this since long before LLMs — every claim links back to its source document.

### 7.3 Rerun flow

`[ RERUN ]` button calls `POST /investigations/{id}/rerun`. TanStack Query's `useMutation` handles it: `onSuccess` pokes the query cache directly (`qc.setQueryData(["investigation", id], data)`) so the new summary is visible immediately, no refetch round-trip.

This run is slow on the user's RTX 4060 (~30s for the full 4-prompt chain on Mistral). A spinner or progress bar would be nice; current UX is just the button label flipping to `[ RUNNING… ]`. Acceptable for stage scope.

---

## 8. IOC pivot graph (`pages/IocPivot.tsx`)

Route: `/iocs/:value` (URL-encoded). Accept any IOC value as the pivot point.

### 8.1 Data flow

1. Fetch `GET /iocs?value=<value>` (LIKE-matched on the server).
2. Filter client-side to *exact* value matches (since the server uses `LIKE %value%`).
3. Aggregate `post_ids` across all matched rows (a single string can be tagged as multiple IOC types — e.g. `okta-sso.help` is both a `domain` and the host part of a `url`).
4. Cap at 30 posts (`MAX_POSTS`), `useQueries` to fetch the detail of each in parallel.
5. Compute co-occurring IOCs across the post set — every IOC in any of the matched posts that *isn't* the pivot value, ranked by occurrence count.

### 8.2 d3-force layout

```ts
forceSimulation<PivotNode>(nodes)
  .force("center", forceCenter(VIEW_W/2, VIEW_H/2))
  .force("charge", forceManyBody().strength(-180))
  .force("link", forceLink(links).distance(160).strength(0.6))
  .force("collide", forceCollide().radius(28))
  .alpha(1).alphaDecay(0.04)
  .on("tick", () => setTick(t => t + 1));
```

Notes:
- **Center IOC node is pinned** (`fx`/`fy` set, not just `x`/`y`). The simulation pushes everything else around the fixed pivot.
- **`alphaDecay: 0.04`** — slightly slower than default (0.0228); gives a more visible settle for the demo.
- **Headless d3** — d3-force only computes positions; we render the SVG ourselves in React. The `setTick` trigger is a forced re-render on every simulation tick. For 30 nodes this is fine; at 1000+ we'd switch to canvas/WebGL.

### 8.3 Co-occurring IOC chips

Below the graph, every other IOC found across the matched post set is rendered as a clickable chip — `ioc_type · value · ×count`. Each chip is a `<Link>` to its own pivot view. Chain pivots: start at one IP, find a domain it co-occurs with, click into that domain, find a BTC wallet, click into that, etc. This is the *one* place the UI actively encourages the analyst to roam through the corpus.

**Industry parallel:** This is what every link-analysis tool (Maltego, Palantir Foundry, IBM i2) is built around. The pattern is universal in CTI — entities are nodes, co-occurrence is edges, and the value is in the chains you uncover.

---

## 9. Watch indicator (`components/WatchIndicator.tsx`)

Mounted in the header, polls `/healthz/full` every 12s via TanStack Query (`refetchInterval: 12_000`). Shows:

- **Status pill** — `IDLE` (all green, no backlog), `PROCESSING · N q` (green, has backlog with total pending count), or `DEGRADED` (red, any check down).
- **Hover popover** — db / ollama / tor row each with status + latency, then a separator, then per-stage pending counts (extraction / llm / mitre).

**Why 12s, not 5s or 30s:** 5s would burn the user's GPU/CPU on every poll for no real benefit (most checks don't change that fast). 30s feels stale during a live demo. 12s is the "fast enough to feel live, slow enough not to nag" sweet spot.

**Why poll, not SSE:** the dashboard needs the *current* health, not a stream of changes. SSE would be over-engineering. Polling is one HTTP every 12s, totally fine.

**Industry parallel:** every monitoring dashboard (Datadog, Grafana, Sentry) has a header-bar status of some kind. The *mechanism* (poll vs SSE vs WebSocket) varies; the *UX* — small persistent indicator with hover-for-detail — is universal.

---

## 10. Boot gate + the App shell (`App.tsx`, `Shell.tsx`)

```tsx
export default function App() {
  const [booted, setBooted] = useState(false);
  return (
    <div className="grain min-h-screen">
      <ShaderBackground />
      <BootSequence onDone={() => setBooted(true)} />
      <div style={{ visibility: booted ? "visible" : "hidden" }}>
        <Header />
        <main>
          <Routes>...</Routes>
        </main>
      </div>
    </div>
  );
}
```

The `visibility: hidden` (not `display: none`) trick keeps the layout mounted during the boot — TanStack Query starts fetching `stats`, `healthzFull`, and `posts` immediately so they're ready when the boot finishes. By the time the user sees the Console page, the data is already there.

---

## 11. The GSAP question (and how we resolved it)

User asked: *should we add GSAP for the boot sequence?*

The answer was no, with reasons:

1. **Bundle impact**: GSAP core + plugins = ~30–40 KB gzipped, all of it loaded *before paint* on the boot page.
2. **Two animation systems**: we already use Framer Motion everywhere else (DetailPanel slide-in, arrival rings, investigation create form, etc.). Adding GSAP means two mental models for animations in the same codebase.
3. **Marginal visual gain**: GSAP's strengths are timeline orchestration and SVG morphing. Neither is a bottleneck for what we built — the 5-phase boot is already cleanly orchestrated with CSS keyframes + Framer.

The recommendation instead: spend the energy on something users see *every session*, not once. We added the spine shimmer + comet trail on the timeline — same level of polish, applied where it compounds.

**General rule this captures:** before adding a dep, ask what fraction of users will *see* the thing it enables, and how often. A library that polishes a one-time experience is a worse trade than the same effort spent polishing the daily experience.

---

## 12. What real CTI tools look like (industry context)

| Tool                          | What it does                                       | What we ape                                                  |
|-------------------------------|----------------------------------------------------|--------------------------------------------------------------|
| **MITRE ATT&CK Navigator**    | Tactic/technique matrix, color-coded per group     | The heatmap layout. Ours is read-only; theirs is editable.   |
| **CrowdStrike Falcon**        | SOC dashboard, threat intel, hunt UI               | The dark-theme + monospaced-data + dense-info pattern.       |
| **Recorded Future**           | Intel cards, citation links to source documents    | The lens-summary citation pattern.                           |
| **Maltego / Palantir / i2**   | Link-analysis graphs, drag-to-explore relationships| The IOC pivot graph, with co-occurring entity chips.         |
| **Splunk SIEM**               | Faceted search, real-time event stream             | The filter chips + SSE-driven live feed.                     |
| **MISP / OpenCTI**            | Open-source threat-intel platforms with REST APIs  | The "structured DB → JSON API → SPA" architecture.           |
| **Shodan / Censys**           | Search engines for internet exposure               | The "click an IOC → see everything that mentions it" flow.   |

We're not trying to replace any of these. We're showing that the same *shapes* — heatmap, link graph, faceted feed, lens-summary-with-citations, header health pill — can be built end-to-end on a small footprint by one person, with every layer instrumented and explained.

---

## 13. Stage exit (per CLAUDE.md §5)

- [x] Code works end-to-end. All five routes load against the live backend; SSE replay + live arrival animations verified.
- [x] `STAGE_07_LEARN.md` written (this file).
- [x] `CLAUDE.md` §3 updated with the Session C + polish summary; §3.5 entry-point now points at Stage 8.
- [x] `PROGRESS.md` has a 2026-05-01 entry covering everything that landed in Stage 7.
- [ ] Git commit — deferred to user. Note: stages 4–7 are all uncommitted at this point; one bundling commit would be the natural pass.

---

## 14. Pointers for the next stage

Stage 8 has two halves: **PDF export per investigation** (WeasyPrint, lens summary as the lead with cited posts as appendices) and **attack graph polish** (a "case file" graph for an investigation that overlays IOCs + MITRE + posts, reusing d3-force from `IocPivot`). Do PDF first — it's the headline feature in `TECHNICAL_PRIMER.md` and the more concrete deliverable. CLAUDE.md §3.5 has the full entry checklist.

The frontend is feature-complete for the demo; the only frontend work in Stage 8 is the export button + maybe the case-file graph view. Don't touch the timecord, watch indicator, heatmap, or investigations layout unless the user explicitly asks.
