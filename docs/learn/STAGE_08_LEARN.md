# Stage 8 — PDF export + case-file attack graph

> The capstone stage. Stages 1–7 turned a synthetic .onion forum into a fully
> enriched, browsable threat-intel dashboard. Stage 8 makes the output
> *shareable* (a styled PDF you can send to someone who isn't running the
> stack) and adds the high-level *connect-the-dots* view (posts, IOCs, and
> MITRE techniques on one canvas, per investigation).
>
> This LEARN doc — like the previous seven — is exhaustive on purpose. Read
> it later, on your own time. The build itself was uninterrupted.

---

## 0. What this stage actually shipped

Two deliverables, both wired into the existing app:

1. **`GET /investigations/{id}/export`** — server-side endpoint that renders
   the chosen investigation as a single PDF. Cover page (metadata + filter +
   lens summary with `[#NNN]` rewritten as numbered footnote anchors) +
   MITRE coverage chart (gradient bar table) + per-post appendices (full
   body, IOCs, entities, technique map). HTML/CSS pipeline rendered by
   **WeasyPrint**.

2. **`/investigations/:id/graph`** — frontend route that renders a single
   d3-force canvas combining:
   - one node per matched post,
   - one node per unique `(ioc_type, value)` across those posts,
   - one node per unique MITRE `technique_id` across those posts,
   - edges connecting each post to its IOCs and techniques.

   Shared IOCs/techniques (i.e. the same value appearing in multiple posts)
   pull their owning posts together in the layout — *that's the entire
   point of the view*: clusters reveal infrastructure shared by otherwise
   unrelated-looking posts.

Two surface buttons in `Investigations.tsx`:
`[ CASE GRAPH ]` → graph route, `[ EXPORT PDF ]` → opens/downloads the PDF.

---

## 1. The PDF half — design and implementation

### 1.1 Why HTML/CSS → PDF (WeasyPrint), not ReportLab

There are three classes of PDF-from-Python tools:

| Class                    | Examples                  | Trade-off                                                              |
|--------------------------|---------------------------|------------------------------------------------------------------------|
| Programmatic PDF builder | `reportlab`, `fpdf2`      | No native deps, ugly to style. Everything is `canvas.drawString(x,y)`. |
| HTML→PDF via browser     | `playwright`, `puppeteer` | Beautiful, but you're shipping a headless Chromium (~150 MB).          |
| HTML→PDF via WebKit/CSS  | `weasyprint`, `wkhtmltopdf` | Beautiful enough, no browser. Native deps (Pango/Cairo/GLib).        |

For a dashboard with already-styled HTML content (a SentinelX investigation
*looks* a certain way), generating PDF from the same paradigm — HTML + CSS —
keeps the design effort in one place. WeasyPrint is the lightest option in
that class. ReportLab would have meant rebuilding the visual identity from
primitives.

### 1.2 The Windows GTK gotcha

WeasyPrint's CSS engine is pure Python; its **text shaping and rendering**
delegates to Pango/Cairo via cffi. Pango/Cairo on Windows live in the
**GTK 3 runtime**. There is no pip-installable equivalent — you install the
GTK runtime as a separate native package and put `bin\` on `PATH`.

Symptom when this is missing:

```
OSError: cannot load library 'gobject-2.0-0': error 0x7e.
Additionally, ctypes.util.find_library() did not manage to locate a library
called 'gobject-2.0-0'
```

What we did:

1. Installed the GTK runtime via the Tomeu Vizoso community installer
   (`tschoonj/GTK-for-Windows-Runtime-Environment-Installer`) to
   `C:\Program Files\GTK3-Runtime Win64\`.
2. Added `C:\Program Files\GTK3-Runtime Win64\bin` to the Windows User PATH.
3. Verified `gobject-2.0-0` is present (named `libgobject-2.0-0.dll` on
   disk; cffi handles the `lib` prefix).
4. Endpoint catches `OSError` separately and returns a clear "GTK runtime
   missing on PATH" error so future sessions don't re-debug from scratch.

There is also a `pydyf` version pin: `weasyprint==62.3` requires
`pydyf<0.11`. The latest pydyf (0.12) renamed an internal `transform`
method, raising `AttributeError: 'super' object has no attribute 'transform'`
on every `write_pdf` call. Pinned pydyf<0.11 in `backend/requirements.txt`.

### 1.3 The render pipeline

`backend/api/export.py::render_investigation_pdf(conn, id)` does this:

1. Fetch the investigation row (no posts join yet — saves a round trip).
2. If a lens summary exists, regex-replace `[#NNN]` with
   `<sup class="cite"><a href="#post-NNN">[k]</a></sup>` where `k` is the
   sequential footnote number — first appearance gets `[1]`, next new id
   gets `[2]`, etc. Repeated citations to the same post share their number.
3. The set of *appendix posts* = the order in which `[#NNN]` appears in the
   summary, then any `summary_post_ids` not yet seen, in order. This way
   matched posts that the LLM didn't cite still appear in the report
   (analyst can decide whether the LLM missed something).
4. For each appendix post, fetch full body + IOCs + entities + techniques.
5. Build a MITRE coverage table: count `(technique_id) → n posts in set`,
   sorted descending, with a CSS gradient bar whose width is `n / max * 100%`.
6. Stitch into one HTML string with embedded `<style>` (a single
   `_PDF_CSS` constant — no external resources, fully self-contained PDF).
7. `HTML(string=html_doc).write_pdf()` → bytes.
8. Return bytes + a sanitised filename (`sentinelx_<id>_<name>.pdf`).

The endpoint sets `Content-Disposition: attachment; filename="..."` so the
browser downloads instead of rendering inline.

### 1.4 CSS quirks worth knowing

PDF CSS is *not quite* browser CSS:

- **`@page`** rules control paper size and margins. We use `A4` and
  `@bottom-right { content: "...page X / Y..."; }` for footers. The `counter()`
  functions are the WeasyPrint mechanism, not a browser-native thing.
- **Page breaks**: `page-break-before: always` on each appendix `<section>`
  guarantees one post per page. `break-inside: avoid` on grid `<li>`s keeps
  IOC/entity items from getting orphaned across pages.
- **`columns: 2` for `<ul.kv>`**: WeasyPrint supports CSS multi-column
  layout, which is how the IOC and entity lists become two-column grids
  without manual layout. Each `<li>` has `break-inside: avoid` so an IOC
  doesn't split across columns.
- **Fonts**: we reference Inter / JetBrains Mono / Consolas, but don't ship
  font files — WeasyPrint falls back to whatever is installed system-wide.
  On Windows that's typically Segoe UI for sans + Consolas for mono, which
  is fine. For a hardened production export, you'd embed `.woff2` files via
  `@font-face` so the PDF renders identically everywhere.
- **Colours**: standard hex / rgb work; `linear-gradient` works on
  backgrounds (used for the MITRE bars). `drop-shadow()` filters work too
  but produce raster pages — we kept it simple with solid fills only.

### 1.5 Why footnote numbers (and not the literal `[#NNN]`)

The summary contains references like `... attributed to a single
threat-actor cluster [#162][#210]`. Three ways to render those in PDF:

- **Leave as text** — looks like debug output.
- **Hyperlinks to a URL** — there's no public URL here; PDFs are offline.
- **Internal anchors** (`<a href="#post-162">`) plus a renumbered display
  (`[1]`) — what we did. WeasyPrint resolves `#post-162` to the
  corresponding `<section id="post-162">` in the appendices, so clicking
  the footnote in a viewer (Chrome's PDF viewer, Acrobat, even SumatraPDF)
  jumps to that post's page. Print-friendly *and* interactive.

This is the same pattern Wikipedia's PDF export uses: numbered citation
markers in the body, full source list in an appendix.

### 1.6 Industry parallels

What we shipped is a tiny, single-tenant analogue of:

- **MISP event reports** — MISP exports threat-intel events as PDF
  ("MISP Report") with metadata, attributes (≈ IOCs), correlations, and
  galaxy/cluster maps (≈ MITRE).
- **Recorded Future / Mandiant Advantage / CrowdStrike Falcon X**
  intelligence briefs — same shape: cover with metadata + summary,
  body of analysis with footnote-style citations to source artefacts,
  appendices with raw IOC tables and ATT&CK navigator screenshots.
- **Splunk SOAR / Palo Alto Cortex XSOAR case reports** — automated
  end-of-incident PDF that bundles the playbook actions, evidence, and
  enrichment into one shareable artefact.

The interesting design constraint, in all of these, is the same: the PDF
is the **only** form a non-platform user ever sees. Executive briefings,
client deliverables, regulatory filings — they all need the same
self-contained, printable artefact. That's why "export as PDF" is rarely
optional in a real CTI/SOC tool.

### 1.7 Production hardening notes (deferred, not done)

If this were going to ship to paying customers:

- **Do PDF rendering in a worker queue.** WeasyPrint can take 1–3 seconds
  on a 50-page report; that's not OK to block a uvicorn worker. Push the
  render onto Celery / RQ / a thread pool, return a job id, poll-or-stream.
- **Paginate the appendix.** Right now we cap at the cited posts (≤ 20 by
  the lens MAX_POSTS constraint). For an investigation with 200 cited
  posts, the PDF would be 250 pages; you'd want a "summary-only" mode plus
  a per-post page.
- **Embed fonts.** As above — only matters for cross-platform fidelity.
- **Sign + watermark.** PDF/A archival signatures via pyHanko, watermarking
  for "DRAFT" / "CONFIDENTIAL". Out of scope here.
- **Bake favicons / branded headers into a `@page` `@top-left` block.** The
  current cover page is functional but plain.

---

## 2. The case-file graph — design and implementation

### 2.1 What problem does this view solve?

By Stage 7, the dashboard had:

- a *temporal* view (Timeline / vertical timecord) — *when* did things happen,
- a *tactical* view (MITRE heatmap) — *what* techniques cluster, in aggregate,
- a *narrative* view (Investigation summary with citations) — *why*,
  framed by a lens,
- a *single-IOC* view (IocPivot) — *which posts share this artefact*.

What was missing: the **case-file** view. Given an investigation's matched
post set, what does the *graph of relationships between those posts and
their artefacts* look like? Which IOCs are shared across posts (= shared
infrastructure or actor footprint)? Which MITRE techniques recur (= shared
tradecraft)?

That's a one-screen question — you want to see *all of it* at once.
Hence: one canvas, three node types, force layout.

### 2.2 Why d3-force (vs Cytoscape, vis-network, GraphViz)

| Option       | Trade-off                                                                                                               |
|--------------|--------------------------------------------------------------------------------------------------------------------------|
| **d3-force** | Tiny (≈ 10 KB), pure-JS, no canvas dependency, runs into our React render loop trivially. Already in the repo for IocPivot. |
| Cytoscape.js | Beautiful but ≈ 400 KB. Worth it for graphs with 1000+ nodes; overkill at 25 posts × ~5 IOCs.                          |
| vis-network  | What `TECHNICAL_PRIMER.md` originally targeted. ≈ 200 KB and depends on canvas. Fine, but no existing code path.       |
| GraphViz     | Server-side static layout (DOT). Beautiful auto-layout, no interactivity. Wrong tool for "click to inspect."           |

We already paid the d3-force cost in Stage 7 Session C. Reusing it for
the case graph keeps the bundle flat.

### 2.3 The data model (client-side)

Three node kinds in `CaseGraph.tsx::CaseNode`:

- `kind: "post"` → one per matched post (cap MAX_POSTS = 25).
- `kind: "ioc"` → one per unique `(ioc_type, value)` pair across those posts.
- `kind: "mitre"` → one per unique `technique_id` across those posts.

A `Map<string, CaseNode>` deduplicates: when post A and post B both
mention `okta-sso.help`, they don't get two ioc nodes — they get one node
with `degree = 2` and two edges (one from each post). The force simulation
then naturally pulls A and B together, because both are anchored to the
same neighbour.

Edges (`SimulationLinkDatum`):
- post → ioc (color: cyan-ish, the IOC's type colour at low alpha),
- post → mitre (color: amber, the brand colour for MITRE in this app),
- never ioc → mitre (no semantic meaning to that edge).

### 2.4 The force simulation tuning

```ts
forceSimulation(nodes)
  .force("center", forceCenter(W/2, H/2))
  .force("charge", forceManyBody().strength(d =>
    d.kind === "post" ? -240 : -120))
  .force("link", forceLink(links).distance(l =>
    (l.target as CaseNode).kind === "ioc" ? 70 : 90).strength(0.5))
  .force("collide", forceCollide().radius(d =>
    d.kind === "post" ? 22 : 14))
  .alpha(1)
  .alphaDecay(0.035)
```

Why these numbers, in plain English:

- **`charge.strength` is the "everyone repels everyone" force.** Stronger
  (more negative) charge on posts (-240) than on artefacts (-120) means
  posts spread out into a comfortable orbit while IOCs and techniques can
  cluster tightly around their owning posts. Without the asymmetry, posts
  would crowd the centre.
- **`link.distance`** sets the resting length of an edge.
  IOCs sit closer (70 px) than MITRE squares (90 px), which gives the
  artefact halo around each post a slight hierarchy — IOCs feel like
  "intimate" attributes, techniques feel like "context" attributes.
  This is purely aesthetic.
- **`collide.radius`** prevents node-on-node overlap. We size it ≈ the
  visual radius of each node type.
- **`alphaDecay = 0.035`** — alpha goes from 1 → ~0.001 in ≈ 130 ticks,
  which at 60 fps is ~2.2 seconds of "jiggle." Long enough to settle into
  a good layout, short enough that the user never thinks "is this stuck?"

Re-rendering pattern: `setTick(t => t + 1)` on every force tick fires a
React render. Cheap because we re-read positions off the live `node.x`,
`node.y` (mutated in place by d3-force) — we never reconcile a "node
state" tree.

### 2.5 The hover-highlight logic

When the cursor is on a node, only that node and its direct neighbours
stay full-opacity; everything else dims to 0.18 alpha. Edges incident to
the hovered node thicken to 1.2 px and stay full opacity; other edges
dim to 0.2 alpha and 0.6 px width.

This is the standard "ego graph" trick: at any given moment, you're
either looking at the *whole* picture (no hover, all nodes visible) or
asking *what is this connected to?* (hover, only the local neighbourhood
visible). It's one of those features that takes 20 lines to add and
disproportionately changes how usable a graph view feels.

Implementation:

```ts
const edgeIndex: Map<string, Set<string>> // node id → neighbour ids
const isHighlighted = (nid) =>
  !hoverNode || hoverNode === nid || edgeIndex.get(hoverNode)?.has(nid);
const dim = (nid) => hoverNode ? (isHighlighted(nid) ? 1 : 0.18) : 1;
```

Built once per `useMemo` along with `nodes` and `links` so the hover path
is O(1).

### 2.6 Why we don't show every node label

Posts get `#id title…` labels (always visible — that's the primary
clickable thing). IOCs get a truncated value label (always visible, but
small font). MITRE squares show the T-code inside the square always; the
*technique name* only on hover.

Reason: with 25 posts × ~3-5 IOCs × 4 techniques each, a fully labelled
canvas would be unreadable. Hover-to-reveal is the only way to keep
density manageable. The graph is for spotting clusters, not for reading
every label.

### 2.7 Click semantics

- Click a **post** node → opens the shared `DetailPanel` (slides in from
  right; same component used by Timeline, Heatmap, Investigations). User
  inspects the post without leaving the graph view.
- Click an **IOC** node → navigates to `/iocs/:value` (the IocPivot view
  from Stage 7, which shows the IOC's full ego network across the *entire*
  corpus, not just this investigation).
- Click a **MITRE** node → no-op for now. Future: navigate to a per-T-code
  view showing which lenses/investigations have flagged it. Not in scope
  for Stage 8.

### 2.8 Industry parallels

The case-file graph is the most direct visual lift in SentinelX from
existing tooling:

- **Maltego** transforms — entity graphs (people, domains, IPs, hashes)
  built up by running "transforms" (queries) against data sources.
  Used heavily in OSINT/CTI investigations. The visual model is identical
  to what we shipped: heterogeneous nodes, force-layout edges, click to
  expand.
- **MISP correlation graph** — given an event, show the events/attributes
  that share IOCs with it. Same shape; same purpose.
- **Splunk SOAR / Palo Alto XSOAR "Investigation View"** — a per-incident
  graph of artefacts (IOCs, hosts, users, processes), often with timeline
  overlays. SOC analysts live in this view during incident response.
- **Mandiant ThreatPursuit / IBM QRadar offense graph** — show the path
  from initial detection through correlated events. Same node-link visual,
  often with MITRE technique overlays.

The pattern is universal because the underlying data is *fundamentally a
graph*: posts/events have IOCs/attributes/observables; observables recur;
recurrence is the signal. A list view buries that. A timeline buries that.
A graph view is the only one that surfaces it directly.

---

## 3. Tech stack added in this stage

| New piece                                  | Where             | Why                                                                                  |
|--------------------------------------------|-------------------|--------------------------------------------------------------------------------------|
| `weasyprint==62.3`                         | backend venv      | HTML/CSS → PDF rendering.                                                            |
| `pydyf<0.11` (pin)                          | backend venv      | Compatibility with weasyprint 62.3 — newer pydyf renames `transform`.               |
| GTK 3 Runtime (Windows)                     | system PATH       | Native libs (Pango, Cairo, GLib) that WeasyPrint links via cffi.                    |
| `backend/api/export.py`                     | new module        | All PDF render logic, no FastAPI imports — testable in isolation.                   |
| `GET /investigations/{id}/export`           | `backend/api/main.py` | Streams the PDF bytes back as `application/pdf` with `Content-Disposition`.     |
| `backend/requirements.txt` (rewritten)      | repo              | Real lockfile-ish thing for the backend. Stages 1–7 had ad-hoc pip installs.        |
| `frontend/src/pages/CaseGraph.tsx`          | frontend          | The graph view itself. ~340 lines of React + d3-force + SVG.                         |
| `/investigations/:id/graph` route           | `App.tsx`         | Routing entry point.                                                                 |
| `[ CASE GRAPH ]` link + `[ EXPORT PDF ]`    | `Investigations.tsx` | Surface the two new features in the place an analyst already lives.              |
| `api.exportInvestigationUrl(id)`            | `frontend/src/lib/api.ts` | Url helper (no fetch wrapper — `<a href>` triggers download natively).        |

Nothing in the existing codebase was changed except for the two
`Investigations.tsx` button additions. The pipeline, the SQLite schema, the
LLM/MITRE/IOC modules, and the three other routes (Home, Timeline,
Heatmap, IocPivot) are all unchanged.

---

## 4. How each piece is used in real industry

| Layer                          | Where you'd find it in industry                                                                                          |
|--------------------------------|--------------------------------------------------------------------------------------------------------------------------|
| HTML/CSS → PDF                 | Every B2B SaaS that produces "shareable reports" (Stripe invoices, GitHub vulnerability reports, Datadog incident PDFs). |
| Footnoted citations in summaries | Threat-intel briefs (Mandiant, Recorded Future), litigation discovery (Relativity), academic LLM systems (Elicit).      |
| MITRE ATT&CK coverage charts   | SOC heatmaps (XDR vendors all ship some version), red-team post-engagement reports, gap-analysis decks for execs.        |
| Force-directed entity graphs    | Maltego, MISP, Splunk SOAR investigations, link-analysis in fraud (Palantir Gotham, Quantexa), bio (STRING, BioGRID).   |
| Hover ego-graph highlighting    | Wikipedia connectivity tools, observable.com graphs, every modern graph DB UI (Neo4j Bloom, ArcadeDB Studio).            |
| GTK runtime as a hidden dep    | Anything Pango/Cairo-based on Windows: GIMP, Inkscape, Pidgin, *and* WeasyPrint. Forever a thorn in cross-platform dev.  |

---

## 5. What's *not* in Stage 8 (and why)

- **PDF preview before download.** Would need a `<iframe>` + the file
  rendered inline (drop the `Content-Disposition`). Out of scope; analysts
  open the file in their PDF viewer of choice.
- **Customisable templates / "report themes."** Single CSS string is
  enough for a learning project. Productising would need Jinja2 +
  per-tenant CSS.
- **MITRE technique node click in CaseGraph.** No `/techniques/:id` page
  exists yet — the heatmap is per-tactic, not per-technique. Future stage.
- **Cytoscape upgrade for >100-node investigations.** d3-force handles ~25
  posts × ~50 artefacts comfortably. Past ~500 nodes you'd want canvas-
  rather-than-SVG rendering and a quadtree. Don't pay that cost yet.
- **PDF rendered server-side as part of the lens rerun.** We could
  pre-bake the PDF whenever the lens is rerun, cache it, return cached
  bytes. The render is fast enough (~1s) that on-demand is fine for now.

---

## 6. The mental model to walk away with

A CTI platform's job is to take a flood of unstructured threat reports and
turn them into:

1. **Structured records** — IOCs, entities, intent, MITRE technique tags.
   (Stages 2–5.)
2. **Browsable views** — timeline, heatmap, single-IOC pivot, single-post
   detail. (Stage 7.)
3. **Narratives** — lens-driven summaries that fuse multiple posts into a
   single argument with citations. (Stage 6.5.)
4. **Shareable artefacts** — *PDFs* that reify the narrative for people
   who don't have access to the platform. (Stage 8 part 1.)
5. **Investigative graph views** — *node-link diagrams* that reveal shared
   infrastructure across the post set, the visual analogue to the
   narrative's footnotes. (Stage 8 part 2.)

Stage 8 is the bridge between "this is a tool the analyst uses" and
"this is a deliverable the analyst's stakeholders consume." Everything
upstream of it produces data; Stage 8 turns that data into things you can
hand to a person who isn't running the stack.

---

**End of Stage 8 LEARN doc. SentinelX I is feature-complete.**
