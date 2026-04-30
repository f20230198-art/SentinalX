# Stage 7 — Frontend Design Language

> Reference inspiration: [avinyr.com](https://www.avinyr.com/) (mood, type, interactivity) and [dark.netflix.io](https://dark.netflix.io/en) (auto-building timeline / family tree).
> This doc is the design brief for the SentinelX dashboard. We are doing a **spiritual port**, not a clone — same vibe, our own execution, original assets.

---

## 1. Mood in one paragraph

A dark, slightly unsettling intelligence terminal. The page should feel like a black-site console booting up: monospace headers, numbered sections, terminal-style flourishes, low-contrast grain, occasional glitch/scramble on text, and a single cold accent color (deep indigo/violet gradient). Motion is purposeful, never decorative: things slide in because data is arriving, lines draw because a relationship was discovered. The Dark-style timeline is the centerpiece — posts and their MITRE techniques wire themselves together as the pipeline runs live.

---

## 2. Palette

| Role | Value | Notes |
|---|---|---|
| Background base | `#0A0612` | Near-black with deep violet undertone, derived from `#13033b` darkened further so text stays readable. |
| Background gradient | `#13033b → #0A0612` | Radial or top-to-bottom on the hero; rest of the page stays flat `#0A0612`. |
| Surface 1 | `#130927` | Cards, panels — same violet family, one step lighter. |
| Surface 2 | `#1C1035` | Hover / elevated. |
| Border | `#2A1F45` | Hairline 1px dividers — violet-tinted, not grey. |
| Text primary | `#E6E3F0` | Off-white with a slight violet cast. |
| Text muted | `#7A7090` | Metadata, timestamps. |
| Accent | `#A78BFA` | Violet-400 (Tailwind). Used sparingly — links, active state, IOC highlights, timeline nodes. Complements the bg naturally. |
| Accent glow | `#7C3AED` at 30% opacity | Subtle glow behind active nodes and CTAs. |
| Accent warn | `#E8A33D` | `llm_unverified` techniques, anomalies. |
| Accent danger | `#E5484D` | High-severity IOCs, threat-actor entities. |
| Grain overlay | 4% opacity SVG noise | Site-wide, fixed-position. |

Rule: **one accent on screen at a time** when possible. The violet palette is cohesive — don't mix in cyan or green. Think deep space surveillance station, not hacker movie.

---

## 3. Typography

- **Display / headers:** a geometric grotesque — `Space Grotesk` or `Geist` (both free, look "engineered"). Tight tracking, weight 500–600.
- **Body:** `Inter` 400/500. Boring on purpose — readability matters for post bodies.
- **Mono (everything technical):** `JetBrains Mono` or `Geist Mono`. Used for: T-codes, IOCs, hashes, timestamps, section numbers `[01]`, terminal flourishes.
- **Scale:** 12 / 14 / 16 / 20 / 28 / 44 / 72. Big jumps. No mid-sizes.
- **Section labels** mimic Avinyr: `[03] // INTELLIGENCE FEED` — bracketed index + `//` separator + uppercase mono.

### Text effects (use sparingly)
- **Scramble-on-reveal** for hero numbers and headers (the "ransomware-decrypt" effect — random chars settle into the real string over ~600ms). Library: a tiny custom hook, or `framer-motion` + a 30-line scrambler. Avinyr uses GSAP's ScrambleText plugin; we don't need GSAP for one effect.
- **Typewriter** for the boot sequence on first load (`> initializing sentinelx_console v0.7...`). Plays once, then fades out. A **[SKIP]** button appears immediately so the user can bypass it — essential for the demo if the examiner wants to skip straight to the data. Does not replay on route changes or page refresh (use `sessionStorage` flag).
- **Glitch hover** on technique T-codes — 80ms RGB-split jitter. CSS-only.

---

## 4. Background & ambience

1. **Site-wide grain.** A fixed `<svg>` with `<feTurbulence>` at 4% opacity, `mix-blend-mode: overlay`. Costs nothing.
2. **Cursor halo.** A soft 400px radial gradient that follows the cursor at low opacity (8–12%). Pure CSS via a CSS variable updated on `mousemove` (throttled). No canvas.
3. **Subtle vignette** on viewport edges, fades to 60% black.
4. **Scanline pass** (optional, toggleable) — 1px horizontal lines at 3% opacity, slowly drifting. Easy to overdo; default OFF, expose as a "CRT mode" toggle for fun.

Things we're explicitly **not** doing: WebGL shaders, three.js scenes, particle fields, audio. They look great on a studio site with one page; on a data dashboard they fight the content and tank perf.

---

## 5. Layout

- **12-col grid**, 24px gutters, max-width 1440px, generous left/right margins.
- **Asymmetric hero:** big number (e.g. live post count) takes 7 cols, status sidebar takes 4, 1 col air. Avinyr does this constantly — never centered, always weighted.
- **Section numbering visible in margins** — `[01]` `[02]` floating in the left gutter as you scroll, sticky-positioned per section.
- **Full-bleed dividers** between major sections — a single hairline rule with the section name in mono on the left and a live counter on the right (`[03] // INTELLIGENCE FEED ……………………… 235 posts / 292 mappings`).

---

## 6. The centerpiece: live attack timeline (Dark-style)

This is the feature that makes the demo land. Concept:

- A vertical timeline (or horizontal — TBD) where every scraped post is a **node** at its `source_created_at` timestamp.
- Each node has **edges** drawing to:
  - the **MITRE technique nodes** it maps to (clustered on one side),
  - **shared-IOC nodes** linking posts that mention the same indicator (the "everything is connected" Dark conceit, applied to threat intel).
- When the pipeline ingests a new post during `--watch`, the node **draws itself in** (stroke-dashoffset SVG animation, ~800ms), then edges trace outward to existing techniques/IOCs over another ~600ms. This is the auto-building behavior from the Dark site.
- Hover a technique → highlight all posts using it, dim the rest.
- Click a node → side panel with full post detail (Stage 6's `/posts/{id}` payload).

**Orientation: horizontal.** Time axis runs left→right. Posts are nodes on the horizontal spine; MITRE technique clusters hang above and below. This is cinematic and works great on the desktop monitor we're building for — no mobile concerns.

**Implementation:** SVG-first, not canvas. ~300 nodes is well within SVG's comfort zone and keeps everything inspectable / accessible / CSS-styleable. If we ever exceed ~2k nodes we revisit with PIXI or regl. Layout via a small force-directed pass on mount (`d3-force`), then frozen — we don't want it jiggling forever. The horizontal spine is fixed; only technique/IOC satellites get force-placed.

**Live updates:** API gets a `GET /events` SSE endpoint in a Stage-7-adjacent backend tweak (small addition, single endpoint). Frontend opens an EventSource, on each event appends + animates the new node. Polling `/posts?limit=20&offset=0` every 5s is the fallback if SSE feels like scope creep.

---

## 7. Interaction details

- **Custom cursor:** a 6px dot + 32px ring that lags behind with spring physics. Ring scales 1.6x and inverts color over interactive elements. ~40 lines of code. Avinyr does this; it's the single cheapest "this feels expensive" trick.
- **Magnetic buttons:** primary CTAs pull toward the cursor within ~80px. Framer Motion's `useMotionValue` + a damped spring.
- **Page transitions:** no full-page fades. Instead, when you click a post, the timeline node *expands into* the detail panel (FLIP / shared-layout transition via `framer-motion` `layoutId`). Keeps the spatial mental model intact.
- **Scroll:** smooth-scroll via `Lenis` (tiny, well-behaved). Pinned sections via `framer-motion`'s `useScroll` + `useTransform` — no GSAP ScrollTrigger needed for what we're doing.
- **CRT/scanline mode:** off by default, toggle button in the top-right corner (a small `[CRT]` mono label). When on, adds 1px horizontal scanlines at 3% opacity + a slight green tint to text. Satisfying easter egg for the demo.

---

## 8. Stack decision

- **React 18 + Vite + TypeScript** (Stage 7 default).
- **Tailwind v4** for styling. Custom palette in `@theme`.
- **Framer Motion** for component animation, layout transitions, scramble/typewriter.
- **Lenis** for smooth scroll.
- **d3-force + plain SVG** for the timeline graph. No vis.js — we want full styling control and the design language demands it.
- **TanStack Query** for API fetching + cache + the polling fallback.
- **Zustand** for tiny global state (cursor position, CRT-mode toggle, selected post).

Explicitly **not** using: GSAP (Framer Motion covers our needs), three.js, vis.js, a component library (Radix only if we need a11y-correct dialogs).

---

## 9. Pages / routes

1. `/` — Console boot → hero stats → live timeline → recent feed. The whole experience on one scroll.
2. `/post/:id` — Full post: body, IOCs, entities, LLM analyses, mapped techniques. Reachable as a side panel from `/` or as its own route.
3. `/techniques` — MITRE corpus browser, filterable.
4. `/technique/:tcode` — Single technique + every post that maps to it.

Stage 8 adds the PDF export button on `/post/:id` and the standalone attack-graph page.

---

## 10. What we borrow vs. what we invent

| From Avinyr | From Dark | Ours |
|---|---|---|
| Numbered section headers `[01] // …` | Auto-drawing node-edge timeline | Domain-specific node types (post / technique / IOC / entity) |
| Boot-sequence intro (one-time) | Edge-trace animation on reveal | Live SSE-driven node insertion during `--watch` |
| Custom cursor + magnetic CTAs | "Everything is connected" mood | Cold cyan accent (theirs is warm sepia/blue) |
| Grain + scanlines + vignette | Centered serif quote moments | Mono-first technical typography |
| GSAP-style scroll choreography | Family-tree generational columns | Time-axis = `source_created_at`, not generations |

---

## 11. Design decisions (locked)

All questions answered. Decisions recorded here for Stage 7 implementation reference.

1. **Accent color:** Deep violet — `#A78BFA` (Tailwind violet-400) on `#13033b`-derived backgrounds. Not cyan, not blue. Palette updated in §2.
2. **Timeline orientation:** **Horizontal.** Time axis left→right, desktop-only. Updated in §6.
3. **First-load boot sequence:** **In, with a [SKIP] button.** Plays once per session (`sessionStorage` flag). Updated in §3.
4. **CRT/scanline mode:** **Toggle button, default off.** Small `[CRT]` label top-right. Updated in §7.
5. **Audio:** None.

Ready to scaffold Stage 7.
