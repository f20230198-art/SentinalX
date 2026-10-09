import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { API_BASE, type TimelinePost } from "../lib/api";
import { SectionDivider } from "../components/Shell";
import { DetailPanel } from "../components/DetailPanel";
import { ProvenanceMark } from "../components/Evidence";
import { intentColor, PROVENANCE, type Provenance } from "../lib/palette";

/* Posts page: every post newest first, grouped by day; live updates via SSE (/events) */

type Filter = "all" | "sale" | "discussion" | "doxxing" | "recruitment" | "with_cve" | "with_btc";

// Filter buttons above the list
const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "sale", label: "Sale" },
  { key: "discussion", label: "Discussion" },
  { key: "recruitment", label: "Recruitment" },
  { key: "doxxing", label: "Doxxing" },
  { key: "with_cve", label: "Mentions a CVE" },
  { key: "with_btc", label: "Has a BTC address" },
];

// Does a post match the chosen filter button?
function passesFilter(p: TimelinePost, f: Filter): boolean {
  if (f === "all") return true;
  if (f === "with_cve") return p.iocs.some((i) => i.ioc_type === "cve");
  if (f === "with_btc") return p.iocs.some((i) => i.ioc_type === "btc");
  return p.intent === f;
}

// Date helpers: "2026-01-31", "14:05", "Sat, Jan 31, 2026"
const dayKey = (t: number) => new Date(t * 1000).toISOString().slice(0, 10);
const timeLabel = (t: number) => new Date(t * 1000).toISOString().slice(11, 16);
function dayLabel(key: string): string {
  return new Date(key + "T00:00:00Z").toLocaleDateString(undefined, {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

// How long a new post stays highlighted, and how long to wait before history counts as loaded
const ARRIVAL_MS = 4500;
const SETTLE_MS = 2500;

// Cache posts across page visits so returning is instant
const cache = { posts: new Map<number, TimelinePost>(), lastId: 0, historyDone: false };

// Posts page
export function Timeline() {
  const [selected, setSelected] = useState<number | null>(null);
  // Filters live in the URL: views are shareable and survive a refresh.
  const [params, setParams] = useSearchParams();
  const filter = (FILTERS.some((f) => f.key === params.get("f")) ? params.get("f") : "all") as Filter;
  const q = params.get("q") ?? "";
  const from = params.get("from");
  const to = params.get("to");
  const setParam = (patch: Record<string, string | null>) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k));
      return next;
    }, { replace: true });
  const setFilter = (f: Filter) => setParam({ f: f === "all" ? null : f });
  // Highlighted row for keyboard navigation (j/k)
  const [cursor, setCursor] = useState(0);
  const searchRef = useRef<HTMLInputElement | null>(null);
  // All posts received so far, and whether the old ones have finished loading
  const [posts, setPosts] = useState<Map<number, TimelinePost>>(cache.posts);
  const [historyDone, setHistoryDone] = useState(cache.historyDone);
  // Posts that just arrived live (shown highlighted)
  const [arriving, setArriving] = useState<Set<number>>(new Set());
  const [connected, setConnected] = useState(true);
  const historyDoneRef = useRef(cache.historyDone);

  // Open the live stream; the server sends old posts first, then new ones as they come in
  useEffect(() => {
    const es = new EventSource(`${API_BASE}/events?since_id=${cache.lastId}`);
    let settle: number | undefined;
    // When no post has arrived for a moment, treat history as fully loaded
    const scheduleSettle = () => {
      window.clearTimeout(settle);
      settle = window.setTimeout(() => {
        historyDoneRef.current = true;
        cache.historyDone = true;
        setHistoryDone(true);
      }, SETTLE_MS);
    };
    if (!cache.historyDone) scheduleSettle(); // an empty corpus still settles

    // Each incoming post: add it to the list (skip duplicates)
    const onPost = (ev: MessageEvent) => {
      let p: TimelinePost & { missing?: boolean };
      try {
        p = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (p.missing) return;
      cache.lastId = Math.max(cache.lastId, p.id);
      setPosts((prev) => {
        if (prev.has(p.id)) return prev;
        const next = new Map(prev).set(p.id, p);
        cache.posts = next;
        return next;
      });
      // After history: briefly highlight the new post
      if (historyDoneRef.current) {
        setArriving((prev) => new Set(prev).add(p.id));
        window.setTimeout(
          () => setArriving((prev) => {
            const next = new Set(prev);
            next.delete(p.id);
            return next;
          }),
          ARRIVAL_MS,
        );
      } else {
        scheduleSettle();
      }
    };
    // Track connection status (the browser reconnects on its own)
    es.addEventListener("post", onPost);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(es.readyState === EventSource.OPEN);
    return () => {
      es.close();
      window.clearTimeout(settle);
    };
  }, []);

  // All posts, newest first
  const all = useMemo(
    () => [...posts.values()].sort((a, b) => b.source_created_at - a.source_created_at),
    [posts],
  );

  // How many posts each filter button would show
  const counts = useMemo(() => {
    const c = Object.fromEntries(FILTERS.map((f) => [f.key, 0])) as Record<Filter, number>;
    for (const p of all) for (const f of FILTERS) if (passesFilter(p, f.key)) c[f.key]++;
    return c;
  }, [all]);

  // All filters except dates (feeds the activity chart)
  const matching = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return all.filter(
      (p) =>
        passesFilter(p, filter) &&
        (!needle ||
          p.thread_title.toLowerCase().includes(needle) ||
          (p.summary ?? "").toLowerCase().includes(needle) ||
          p.body_preview.toLowerCase().includes(needle) ||
          p.iocs.some((i) => i.value.toLowerCase().includes(needle)) ||
          p.techniques.some((t) => t.technique_id.toLowerCase() === needle)),
    );
  }, [all, filter, q]);

  // Then apply the date range picked on the chart
  const visible = useMemo(
    () =>
      matching.filter((p) => {
        const d = dayKey(p.source_created_at);
        return (!from || d >= from) && (!to || d <= to);
      }),
    [matching, from, to],
  );

  // j / k / Enter / "/" — keyboard triage through the visible rows.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.closest("input, textarea, select");
      if (e.key === "/" && !typing) {
        e.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (typing || selected !== null) return;
      if (e.key === "j" || e.key === "k") {
        e.preventDefault();
        setCursor((c) => Math.max(0, Math.min(visible.length - 1, c + (e.key === "j" ? 1 : -1))));
      } else if (e.key === "Enter" && visible[cursor]) {
        setSelected(visible[cursor].id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, cursor, selected]);
  useEffect(() => setCursor(0), [filter, q, from, to]);
  const cursorId = visible[cursor]?.id;
  // Keep the highlighted row scrolled into view
  useEffect(() => {
    if (cursorId !== undefined)
      document.getElementById(`post-row-${cursorId}`)?.scrollIntoView({ block: "nearest" });
  }, [cursorId]);

  // Group visible posts by day
  const days = useMemo(() => {
    const m = new Map<string, TimelinePost[]>();
    for (const p of visible) {
      const k = dayKey(p.source_created_at);
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(p);
    }
    return [...m.entries()];
  }, [all, filter]);

  return (
    <div className="mx-auto max-w-[1440px] px-4 pb-24 sm:px-8">
      <SectionDivider
        label="Posts"
        trailing={historyDone ? `${all.length} posts` : `Loading ${all.length} posts…`}
      />

      {/* Filter buttons + live status */}
      <div className="mb-6 flex flex-wrap items-center gap-x-6 gap-y-3">
        <div role="group" aria-label="Filter posts" className="flex flex-wrap gap-1.5">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              aria-pressed={filter === f.key}
              className={`border px-2.5 py-1 text-sm font-semibold ${
                filter === f.key
                  ? "border-text bg-text text-surface-1"
                  : "border-border-soft text-text-muted hover:border-text hover:text-text"
              }`}
            >
              {f.label}
              <span className="ml-1.5 font-normal tabular-nums opacity-70">{counts[f.key]}</span>
            </button>
          ))}
        </div>
        <span className="ml-auto inline-flex items-center gap-2 text-sm" aria-live="polite">
          <span
            aria-hidden
            className={`inline-block h-2 w-2 rounded-full ${
              !connected ? "border-2 border-warn" : historyDone ? "bg-ok" : "border-2 border-text-muted"
            }`}
          />
          <span className={!connected ? "text-warn" : "text-text-muted"}>
            {!connected
              ? "Live feed disconnected, reconnecting…"
              : historyDone
                ? "Live: new posts appear at the top"
                : "Replaying history…"}
          </span>
        </span>
      </div>

      {/* Search box */}
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <label className="relative min-w-[260px] flex-1">
          <span className="sr-only">Search posts</span>
          <input
            ref={searchRef}
            value={q}
            onChange={(e) => setParam({ q: e.target.value || null })}
            placeholder="Search titles, summaries, IOCs or a T-code…"
            className="w-full border border-border-soft bg-surface-1 px-3 py-2 text-sm focus:border-text focus:outline-none"
          />
          <kbd className="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 border border-border-soft px-1.5 font-mono text-xs text-text-muted">/</kbd>
        </label>
        <span className="text-xs text-text-muted">
          <kbd className="font-mono">j</kbd>/<kbd className="font-mono">k</kbd> move · <kbd className="font-mono">Enter</kbd> open ·{" "}
          <kbd className="font-mono">Esc</kbd> close
        </span>
      </div>

      {/* Posts-per-day chart */}
      {historyDone && matching.length > 0 && (
        <ActivityChart
          posts={matching}
          from={from}
          to={to}
          onRange={(a, b) => setParam({ from: a, to: b })}
        />
      )}

      {/* Legend for the technique marks */}
      <div className="mb-4 flex flex-wrap gap-x-5 gap-y-1 text-xs text-text-muted">
        {(Object.keys(PROVENANCE) as Provenance[]).map((k) => (
          <span key={k} className="inline-flex items-center gap-1.5" title={PROVENANCE[k].description}>
            <ProvenanceMark source={k} /> {PROVENANCE[k].label}
          </span>
        ))}
      </div>

      {/* Loading / empty states */}
      {all.length === 0 && !historyDone && <p className="py-16 text-sm text-text-muted">Loading the register…</p>}
      {historyDone && days.length === 0 && (
        <p className="border-t border-border-soft py-8 text-sm text-text-muted">
          {all.length === 0 ? "No posts ingested yet. Run the scraper or a Scout job." : "No posts match these filters."}{" "}
          {all.length > 0 && (
            <button className="font-semibold text-accent underline" onClick={() => setParams({}, { replace: true })}>
              Clear all filters
            </button>
          )}
        </p>
      )}

      {/* The posts table, one block per day */}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[880px] border-collapse text-sm">
          <thead className="sr-only">
            <tr>
              <th>Post</th><th>Time (UTC)</th><th>Thread</th><th>Intent</th><th>Techniques</th><th>IOCs</th>
            </tr>
          </thead>
          {days.map(([key, list]) => (
            <tbody key={key}>
              <tr>
                <th colSpan={6} scope="colgroup" className="border-b-2 border-rule pt-8 pb-1.5 text-left">
                  <span className="text-[1rem] font-bold">{dayLabel(key)}</span>
                  <span className="ml-3 font-normal text-text-muted tabular-nums">{list.length} posts</span>
                </th>
              </tr>
              {list.map((p) => (
                <PostRow
                  key={p.id}
                  post={p}
                  active={p.id === cursorId}
                  arriving={arriving.has(p.id)}
                  onOpen={() => setSelected(p.id)}
                />
              ))}
            </tbody>
          ))}
        </table>
      </div>

      <DetailPanel id={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

// One row: id, time, title, intent, first 4 techniques, IOC count
function PostRow({
  post,
  arriving,
  active,
  onOpen,
}: {
  post: TimelinePost;
  arriving: boolean;
  active: boolean;
  onOpen: () => void;
}) {
  const techs = post.techniques.slice(0, 4);
  const translated = post.lang && post.lang !== "en" && post.lang !== "unknown";
  return (
    <tr
      id={`post-row-${post.id}`}
      onClick={onOpen}
      aria-selected={active}
      className={`cursor-pointer border-b border-border-soft align-baseline transition-[background-color,box-shadow] duration-700 hover:bg-surface-1 ${
        active ? "bg-surface-1 outline outline-2 -outline-offset-2 outline-text " : ""
      }${
        arriving ? "bg-surface-1 shadow-[inset_3px_0_0_var(--color-accent)] [animation:row-in_600ms_cubic-bezier(0.16,1,0.3,1)]" : ""
      }`}
    >
      <td className="w-16 py-2.5 pr-3 pl-2">
        <button
          onClick={(e) => {
            e.stopPropagation();
            onOpen();
          }}
          className="font-mono text-text-muted hover:text-accent"
          aria-label={`Open post ${post.id}`}
        >
          #{post.id}
        </button>
      </td>
      <td className="w-14 py-2.5 pr-4 text-text-muted tabular-nums">{timeLabel(post.source_created_at)}</td>
      <td className="py-2.5 pr-4">
        <span className="font-semibold">{post.thread_title}</span>
        <span className="ml-2 text-xs text-text-muted">{post.category}</span>
        {translated && (
          <span className="ml-2 border border-border-soft px-1 text-xs text-text-muted" title="Translated before analysis">
            {post.lang!.toUpperCase()}–EN
          </span>
        )}
        {arriving && <span className="ml-2 text-xs font-bold text-accent">New</span>}
      </td>
      <td className="w-28 py-2.5 pr-4 font-semibold" style={{ color: intentColor(post.intent) }}>
        {post.intent ?? <span className="font-normal text-text-muted">—</span>}
      </td>
      <td className="w-64 py-2.5 pr-4">
        <span className="flex flex-wrap gap-x-3 gap-y-1">
          {techs.map((t) => (
            <span
              key={t.technique_id + t.source}
              className="inline-flex items-center gap-1.5 font-mono text-xs"
              title={`${t.name ?? "Not in corpus"} · ${PROVENANCE[t.source as Provenance]?.label ?? t.source}`}
            >
              <ProvenanceMark source={t.source} size={8} />
              {t.technique_id}
            </span>
          ))}
          {post.techniques.length > techs.length && (
            <span className="text-xs text-text-muted">+{post.techniques.length - techs.length}</span>
          )}
        </span>
      </td>
      <td className="w-24 py-2.5 pr-2 text-right text-xs text-text-muted tabular-nums">
        {post.iocs.length > 0 ? `${post.iocs.length} IOC${post.iocs.length > 1 ? "s" : ""}` : "—"}
      </td>
    </tr>
  );
}

/* Posts-per-day chart; drag or click to filter by date (saved in URL) */
function ActivityChart({
  posts,
  from,
  to,
  onRange,
}: {
  posts: TimelinePost[];
  from: string | null;
  to: string | null;
  onRange: (from: string | null, to: string | null) => void;
}) {
  // Count posts per day, split into "sale" and everything else
  const bars = useMemo(() => {
    const m = new Map<string, { sale: number; other: number }>();
    for (const p of posts) {
      const k = dayKey(p.source_created_at);
      const b = m.get(k) ?? { sale: 0, other: 0 };
      p.intent === "sale" ? b.sale++ : b.other++;
      m.set(k, b);
    }
    // Fill gaps so the x-axis is real time, not just days that had posts.
    const keys = [...m.keys()].sort();
    const out: { day: string; sale: number; other: number }[] = [];
    if (!keys.length) return out;
    for (let d = new Date(keys[0] + "T00:00:00Z"); d <= new Date(keys[keys.length - 1] + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + 1)) {
      const k = d.toISOString().slice(0, 10);
      out.push({ day: k, ...(m.get(k) ?? { sale: 0, other: 0 }) });
    }
    return out;
  }, [posts]);

  // Chart size and bar width
  const W = 1200, H = 120, PAD = 18;
  const max = Math.max(1, ...bars.map((b) => b.sale + b.other));
  const bw = (W - PAD * 2) / Math.max(1, bars.length);
  const svgRef = useRef<SVGSVGElement | null>(null);
  // Start/end bar of the current drag
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragTo, setDragTo] = useState<number | null>(null);

  // Which bar is under the mouse
  const idxAt = (clientX: number) => {
    const r = svgRef.current!.getBoundingClientRect();
    const x = ((clientX - r.left) / r.width) * W;
    return Math.max(0, Math.min(bars.length - 1, Math.floor((x - PAD) / bw)));
  };
  // Is bar i inside the selected range?
  const inSel = (i: number) => {
    if (dragFrom !== null && dragTo !== null) return i >= Math.min(dragFrom, dragTo) && i <= Math.max(dragFrom, dragTo);
    const d = bars[i].day;
    return (!from || d >= from) && (!to || d <= to);
  };
  const hasRange = !!(from || to);
  const fmtDay = (k: string) =>
    new Date(k + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });

  return (
    <figure className="m-0 mb-6">
      <div className="mb-1 flex items-baseline gap-4 text-xs text-text-muted">
        <span className="font-bold text-text">Activity</span>
        <span><span className="mr-1 inline-block h-2.5 w-2.5 bg-accent align-middle" />Sale</span>
        <span><span className="mr-1 inline-block h-2.5 w-2.5 bg-text align-middle" />Other intents</span>
        <span className="ml-auto">
          {hasRange ? (
            <>
              {from ? fmtDay(from) : "start"} – {to ? fmtDay(to) : "now"} ·{" "}
              <button className="font-semibold text-accent underline" onClick={() => onRange(null, null)}>
                Clear range
              </button>
            </>
          ) : (
            "Drag across the chart to filter by date"
          )}
        </span>
      </div>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="block h-28 w-full cursor-crosshair touch-none select-none border-b border-text"
        preserveAspectRatio="none"
        role="img"
        aria-label={`Posts per day, ${bars.length} days`}
        // Drag across bars to pick a date range
        onPointerDown={(e) => {
          (e.target as Element).setPointerCapture?.(e.pointerId);
          const i = idxAt(e.clientX);
          setDragFrom(i);
          setDragTo(i);
        }}
        onPointerMove={(e) => dragFrom !== null && setDragTo(idxAt(e.clientX))}
        onPointerUp={() => {
          if (dragFrom === null || dragTo === null) return;
          const a = Math.min(dragFrom, dragTo), b = Math.max(dragFrom, dragTo);
          onRange(bars[a].day, bars[b].day);
          setDragFrom(null);
          setDragTo(null);
        }}
      >
        {/* One stacked bar per day: sale (accent) under other intents */}
        {bars.map((b, i) => {
          const x = PAD + i * bw;
          const hOther = ((H - 8) * b.other) / max;
          const hSale = ((H - 8) * b.sale) / max;
          const on = !hasRange && dragFrom === null ? true : inSel(i);
          return (
            <g key={b.day} opacity={on ? 1 : 0.2}>
              <title>{`${fmtDay(b.day)}: ${b.sale + b.other} posts (${b.sale} sale)`}</title>
              <rect x={x + 1} y={H - hSale} width={Math.max(1, bw - 2)} height={hSale} fill="var(--color-accent)" />
              <rect x={x + 1} y={H - hSale - hOther} width={Math.max(1, bw - 2)} height={hOther} fill="var(--color-text)" />
            </g>
          );
        })}
      </svg>
      <div className="mt-1 flex justify-between text-xs text-text-muted tabular-nums">
        <span>{bars[0] && fmtDay(bars[0].day)}</span>
        <span>{bars.length > 0 && `peak ${max} posts/day`}</span>
        <span>{bars.length > 0 && fmtDay(bars[bars.length - 1].day)}</span>
      </div>
    </figure>
  );
}
