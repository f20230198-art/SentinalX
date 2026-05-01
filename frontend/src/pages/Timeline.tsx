import { useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { API_BASE, type TimelinePost } from "../lib/api";
import { SectionDivider } from "../components/Shell";
import { DetailPanel, TECH_COLOR } from "../components/DetailPanel";
import { useLivePosts } from "../hooks/useLivePosts";

/* ----------------------------------------------------------------------- *
 * Vertical timecord — Dark-inspired layout.
 *
 * A single glowing violet thread runs top → bottom down the page. Posts
 * appear as branches off the thread, alternating left ↔ right. Day markers
 * (date pills) float on the spine; within a day, branches are stacked in
 * chronological order. Newest day at the top.
 *
 * Behaviour preserved from the previous horizontal version:
 *   - SSE bootstrap (replay everything → switch to live stream).
 *   - Filter chips dim non-matching branches.
 *   - Hover a branch → fan its MITRE techniques out as a halo.
 *   - Click → DetailPanel.
 *   - Live arrival animation: pulsing rings + [NEW] flag on the node.
 * ----------------------------------------------------------------------- */

const ROW_H = 110; // vertical space per branch
const DAY_HEAD_H = 56;
const SPINE_X_PCT = 50;
const NODE_R = 7;

const INTENT_COLOR: Record<string, string> = {
  sale: "rgb(232, 163, 61)",
  doxxing: "rgb(229, 72, 77)",
  recruitment: "rgb(125, 211, 252)",
};
const intentColor = (intent: string | null) =>
  (intent && INTENT_COLOR[intent]) || "rgb(167, 139, 250)";

type Filter =
  | "all"
  | "sale"
  | "discussion"
  | "doxxing"
  | "recruitment"
  | "with_cve"
  | "with_btc";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "ALL" },
  { key: "sale", label: "SALE" },
  { key: "discussion", label: "DISCUSSION" },
  { key: "doxxing", label: "DOXXING" },
  { key: "recruitment", label: "RECRUITMENT" },
  { key: "with_cve", label: "WITH CVE" },
  { key: "with_btc", label: "WITH BTC" },
];

function passesFilter(p: TimelinePost, f: Filter): boolean {
  if (f === "all") return true;
  if (f === "sale" || f === "discussion" || f === "doxxing" || f === "recruitment") {
    return p.intent === f;
  }
  if (f === "with_cve") return p.iocs.some((i) => i.ioc_type === "cve");
  if (f === "with_btc") return p.iocs.some((i) => i.ioc_type === "btc");
  return true;
}

function dayKey(t: number): string {
  return new Date(t * 1000).toISOString().slice(0, 10);
}
function dayLabel(key: string): string {
  const d = new Date(key + "T00:00:00Z");
  return d
    .toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "2-digit",
      timeZone: "UTC",
    })
    .toUpperCase();
}
function timeLabel(t: number): string {
  return new Date(t * 1000).toISOString().slice(11, 16) + " UTC";
}

interface DayGroup {
  key: string;
  posts: TimelinePost[];
}

function groupByDay(posts: TimelinePost[]): DayGroup[] {
  const m = new Map<string, TimelinePost[]>();
  for (const p of posts) {
    const k = dayKey(p.source_created_at);
    if (!m.has(k)) m.set(k, []);
    m.get(k)!.push(p);
  }
  // Newest day first, newest post within day first → reads top-down like a feed.
  const groups = [...m.entries()].map(([key, list]) => ({
    key,
    posts: list.sort((a, b) => b.source_created_at - a.source_created_at),
  }));
  groups.sort((a, b) => (a.key < b.key ? 1 : -1));
  return groups;
}

export function Timeline() {
  const [selected, setSelected] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [arrivingIds, setArrivingIds] = useState<Set<number>>(new Set());

  const markArriving = (id: number) => {
    setArrivingIds((prev) => new Set(prev).add(id));
    window.setTimeout(() => {
      setArrivingIds((prev) => {
        if (!prev.has(id)) return prev;
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }, 4500);
  };

  const [posts, setPosts] = useState<TimelinePost[]>([]);
  const [historyDone, setHistoryDone] = useState(false);

  useEffect(() => {
    const es = new EventSource(`${API_BASE}/events?since_id=0`);
    let count = 0;
    let settleTimer: number | undefined;
    const scheduleSettle = () => {
      if (settleTimer) window.clearTimeout(settleTimer);
      settleTimer = window.setTimeout(() => {
        if (count > 0) setHistoryDone(true);
      }, 2500);
    };
    const onPost = (ev: MessageEvent) => {
      try {
        const p = JSON.parse(ev.data) as TimelinePost;
        if ((p as { missing?: boolean }).missing) return;
        count++;
        setPosts((prev) =>
          prev.some((x) => x.id === p.id) ? prev : [...prev, p],
        );
        scheduleSettle();
      } catch {
        /* ignore */
      }
    };
    es.addEventListener("post", onPost);
    return () => {
      es.removeEventListener("post", onPost);
      es.close();
      if (settleTimer) window.clearTimeout(settleTimer);
    };
  }, []);

  const liveAfter = historyDone
    ? Math.max(0, ...posts.map((p) => p.id))
    : null;
  const livePosts = useLivePosts(liveAfter);

  useEffect(() => {
    for (const p of livePosts) {
      if (!arrivingIds.has(p.id)) markArriving(p.id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [livePosts]);

  const merged = useMemo(() => {
    const m = new Map<number, TimelinePost>();
    for (const p of posts) m.set(p.id, p);
    for (const p of livePosts) m.set(p.id, p);
    return [...m.values()];
  }, [posts, livePosts]);

  const days = useMemo(() => groupByDay(merged), [merged]);

  const counts = useMemo(() => {
    const c: Record<Filter, number> = {
      all: merged.length,
      sale: 0,
      discussion: 0,
      doxxing: 0,
      recruitment: 0,
      with_cve: 0,
      with_btc: 0,
    };
    for (const p of merged) {
      if (p.intent && c[p.intent as Filter] !== undefined) c[p.intent as Filter]++;
      if (p.iocs.some((i) => i.ioc_type === "cve")) c.with_cve++;
      if (p.iocs.some((i) => i.ioc_type === "btc")) c.with_btc++;
    }
    return c;
  }, [merged]);

  const replayLatest = () => {
    const newest = merged.reduce(
      (best, p) => (p.id > (best?.id ?? -1) ? p : best),
      null as TimelinePost | null,
    );
    if (newest) {
      markArriving(newest.id);
      setSelected(newest.id);
    }
  };

  return (
    <div className="max-w-[1440px] mx-auto px-8">
      <SectionDivider
        index="02"
        label="Intelligence feed"
        trailing={
          historyDone
            ? `${merged.length} posts · live`
            : `loading ${merged.length} posts…`
        }
      />

      {/* Filter chips */}
      <div className="mb-4 flex flex-wrap gap-2 font-mono text-[10px] tracking-[0.18em]">
        {FILTERS.map((f) => {
          const n = counts[f.key];
          const active = filter === f.key;
          return (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`px-3 py-1.5 border transition-colors ${
                active
                  ? "border-accent text-accent bg-accent/10"
                  : "border-border-soft text-text-muted hover:text-text hover:border-text-muted"
              }`}
            >
              {f.label}{" "}
              <span className="opacity-60 ml-1 tabular-nums">{n}</span>
            </button>
          );
        })}
      </div>

      <ArrivalBanner ids={[...arrivingIds]} merged={merged} />

      {/* Live status pill */}
      <div className="mb-3 flex items-center justify-end gap-3 font-mono text-[10px] tracking-[0.2em] text-text-muted">
        <button
          onClick={replayLatest}
          disabled={!historyDone || merged.length === 0}
          className="border border-border-soft px-2 py-1 hover:text-accent hover:border-accent disabled:opacity-30 transition-colors"
          title="Re-fire the entrance animation on the most recent post"
        >
          [ REPLAY LIVE ]
        </button>
        <span className="flex items-center gap-2">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent shadow-[0_0_6px_var(--color-accent)] animate-pulse" />
          {historyDone ? "LIVE" : "INDEXING"}
        </span>
      </div>

      {/* The timecord itself */}
      <div className="relative">
        {/* Spine */}
        <div
          className="absolute top-0 bottom-0 w-px"
          style={{
            left: `${SPINE_X_PCT}%`,
            background:
              "linear-gradient(to bottom, transparent 0%, rgba(167,139,250,0.55) 6%, rgba(167,139,250,0.55) 94%, transparent 100%)",
            boxShadow: "0 0 12px rgba(167,139,250,0.45)",
          }}
        />
        {/* Spine head + tail caps */}
        <div
          className="absolute -translate-x-1/2 w-2 h-2 rounded-full bg-accent"
          style={{
            left: `${SPINE_X_PCT}%`,
            top: 0,
            boxShadow: "0 0 14px rgba(167,139,250,0.9)",
          }}
        />

        {/* Shimmer pulse — fires when new intel arrives, races down the spine. */}
        <AnimatePresence>
          {arrivingIds.size > 0 && (
            <motion.div
              key={[...arrivingIds].join(",")}
              initial={{ top: 0, opacity: 0 }}
              animate={{ top: "100%", opacity: [0, 1, 1, 0] }}
              transition={{ duration: 1.6, ease: "easeOut", times: [0, 0.1, 0.85, 1] }}
              className="absolute -translate-x-1/2 pointer-events-none"
              style={{ left: `${SPINE_X_PCT}%` }}
            >
              <div
                className="w-0.5 h-24 rounded-full"
                style={{
                  background:
                    "linear-gradient(to bottom, transparent 0%, rgba(255,255,255,0.95) 40%, rgb(167,139,250) 60%, transparent 100%)",
                  boxShadow:
                    "0 0 24px rgba(167,139,250,0.95), 0 0 48px rgba(167,139,250,0.5)",
                }}
              />
            </motion.div>
          )}
        </AnimatePresence>

        {days.length === 0 && !historyDone && (
          <div className="py-20 text-center font-mono text-xs text-text-muted">
            indexing intel feed…
          </div>
        )}

        <div className="relative">
          {days.map((day) => (
            <DayBlock
              key={day.key}
              day={day}
              filter={filter}
              hovered={hovered}
              selected={selected}
              arrivingIds={arrivingIds}
              onHover={setHovered}
              onSelect={setSelected}
            />
          ))}
        </div>
      </div>

      {/* Legend */}
      <div className="mt-6 flex flex-wrap gap-x-6 gap-y-1 font-mono text-[10px] tracking-[0.15em] text-text-muted">
        <LegendDot color="rgb(167, 139, 250)" label="DISCUSSION" />
        <LegendDot color="rgb(232, 163, 61)" label="SALE" />
        <LegendDot color="rgb(229, 72, 77)" label="DOXXING" />
        <LegendDot color="rgb(125, 211, 252)" label="RECRUITMENT" />
        <span className="ml-auto opacity-70">
          hover a node for its MITRE techniques · click for detail
        </span>
      </div>

      <DetailPanel id={selected} onClose={() => setSelected(null)} />

      <div className="h-32" />
    </div>
  );
}

function DayBlock({
  day,
  filter,
  hovered,
  selected,
  arrivingIds,
  onHover,
  onSelect,
}: {
  day: DayGroup;
  filter: Filter;
  hovered: number | null;
  selected: number | null;
  arrivingIds: Set<number>;
  onHover: (id: number | null) => void;
  onSelect: (id: number) => void;
}) {
  return (
    <div className="relative">
      {/* Day marker on the spine */}
      <div
        className="relative flex items-center justify-center"
        style={{ height: DAY_HEAD_H }}
      >
        <div
          className="absolute left-1/2 -translate-x-1/2 px-3 py-1 border border-accent/60 bg-base/90 backdrop-blur-sm font-mono text-[10px] tracking-[0.25em] text-accent"
          style={{ boxShadow: "0 0 18px rgba(167,139,250,0.35)" }}
        >
          {dayLabel(day.key)}
          <span className="ml-2 text-text-muted">· {day.posts.length}</span>
        </div>
      </div>

      {day.posts.map((p, i) => (
        <PostBranch
          key={p.id}
          post={p}
          side={i % 2 === 0 ? "right" : "left"}
          dimmed={filter !== "all" && !passesFilter(p, filter)}
          hot={hovered === p.id || selected === p.id}
          arriving={arrivingIds.has(p.id)}
          onHover={onHover}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

function PostBranch({
  post,
  side,
  dimmed,
  hot,
  arriving,
  onHover,
  onSelect,
}: {
  post: TimelinePost;
  side: "left" | "right";
  dimmed: boolean;
  hot: boolean;
  arriving: boolean;
  onHover: (id: number | null) => void;
  onSelect: (id: number) => void;
}) {
  const color = intentColor(post.intent);
  const techs = post.techniques.slice(0, 6);

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: dimmed ? 0.18 : 1, y: 0 }}
      transition={{ duration: 0.35 }}
      className="relative"
      style={{ height: ROW_H }}
      onMouseEnter={() => onHover(post.id)}
      onMouseLeave={() => onHover(null)}
    >
      {/* Branch line */}
      <div
        className="absolute top-1/2 h-px"
        style={{
          left: side === "right" ? "50%" : "calc(50% - 18%)",
          width: "18%",
          background: `linear-gradient(${
            side === "right" ? "to right" : "to left"
          }, ${color}, transparent)`,
          opacity: hot ? 0.95 : 0.55,
        }}
      />

      {/* Node on the spine */}
      <button
        onClick={() => onSelect(post.id)}
        className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 group"
        style={{ left: `${SPINE_X_PCT}%` }}
      >
        {arriving && [0, 0.35, 0.7, 1.05].map((delay, i) => (
          <motion.span
            key={i}
            initial={{ scale: 0, opacity: 1 }}
            animate={{ scale: 8, opacity: 0 }}
            transition={{ duration: 2.6, delay, ease: "easeOut" }}
            className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-6 h-6 rounded-full border-2 border-accent"
            style={{ filter: "drop-shadow(0 0 12px rgba(167,139,250,0.9))" }}
          />
        ))}
        {arriving && (
          <motion.span
            initial={{ opacity: 0, scaleY: 0 }}
            animate={{ opacity: [0, 0.85, 0], scaleY: [0, 1, 1] }}
            transition={{ duration: 4, ease: "easeOut" }}
            className="absolute left-1/2 top-1/2 -translate-x-1/2 origin-top w-px pointer-events-none"
            style={{
              height: 64,
              background:
                "linear-gradient(to bottom, rgb(167,139,250), transparent)",
              filter: "drop-shadow(0 0 6px rgba(167,139,250,0.8))",
            }}
          />
        )}
        <span
          className="block rounded-full transition-all"
          style={{
            width: hot || arriving ? NODE_R * 2.6 : NODE_R * 2,
            height: hot || arriving ? NODE_R * 2.6 : NODE_R * 2,
            backgroundColor: color,
            boxShadow: hot
              ? `0 0 18px ${color}`
              : `0 0 8px ${color}`,
            border: arriving ? "1.5px solid white" : "none",
          }}
        />
      </button>

      {/* Card on the chosen side */}
      <button
        onClick={() => onSelect(post.id)}
        className={`absolute top-1/2 -translate-y-1/2 w-[36%] text-${
          side === "right" ? "left" : "right"
        } px-4 py-3 border bg-base/70 backdrop-blur-sm transition-colors ${
          hot
            ? "border-accent bg-accent/5"
            : "border-border-soft hover:border-text-muted"
        }`}
        style={{
          [side === "right" ? "left" : "right"]: "calc(50% + 4%)",
        } as React.CSSProperties}
      >
        <div
          className={`flex items-baseline gap-2 font-mono text-[10px] tracking-[0.18em] text-text-muted ${
            side === "left" ? "flex-row-reverse" : ""
          }`}
        >
          <span className="text-accent">#{post.id}</span>
          <span>{post.category.toUpperCase()}</span>
          <span className="opacity-60">{timeLabel(post.source_created_at)}</span>
          {post.intent && (
            <span
              className={`${
                side === "left" ? "mr-auto" : "ml-auto"
              } px-1.5 py-px border`}
              style={{ borderColor: color, color }}
            >
              {post.intent.toUpperCase()}
            </span>
          )}
        </div>
        <div className="mt-1 font-display text-sm leading-tight text-text">
          {post.thread_title}
        </div>
        {techs.length > 0 && (
          <div
            className={`mt-2 flex flex-wrap gap-1 ${
              side === "left" ? "justify-end" : ""
            }`}
          >
            {techs.map((t) => (
              <span
                key={t.technique_id + t.source}
                className="font-mono text-[9px] px-1.5 py-px border"
                style={{
                  borderColor: TECH_COLOR[t.source] ?? "rgb(167,139,250)",
                  color: TECH_COLOR[t.source] ?? "rgb(167,139,250)",
                }}
                title={t.name ?? ""}
              >
                {t.technique_id}
              </span>
            ))}
            {post.techniques.length > techs.length && (
              <span className="font-mono text-[9px] text-text-muted">
                +{post.techniques.length - techs.length}
              </span>
            )}
          </div>
        )}
        {arriving && (
          <motion.span
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className={`absolute top-1 ${
              side === "right" ? "right-2" : "left-2"
            } font-mono text-[9px] tracking-[0.2em] text-accent`}
          >
            [ NEW ]
          </motion.span>
        )}
      </button>
    </motion.div>
  );
}

function ArrivalBanner({
  ids,
  merged,
}: {
  ids: number[];
  merged: TimelinePost[];
}) {
  const newestId = ids.length ? Math.max(...ids) : null;
  const post =
    newestId !== null ? merged.find((p) => p.id === newestId) ?? null : null;
  return (
    <AnimatePresence>
      {post && (
        <motion.div
          key={post.id}
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -10 }}
          transition={{ duration: 0.25 }}
          className="mb-3 flex items-center gap-3 border border-accent bg-accent/10 px-4 py-3 font-mono text-xs"
          style={{ boxShadow: "0 0 30px rgba(167, 139, 250, 0.45)" }}
        >
          <motion.span
            animate={{ opacity: [1, 0.3, 1] }}
            transition={{ duration: 0.8, repeat: 3 }}
            className="inline-block w-2 h-2 rounded-full bg-accent shadow-[0_0_10px_var(--color-accent)]"
          />
          <span className="text-accent tracking-[0.2em] font-bold">
            NEW INTEL · #{post.id}
          </span>
          <span className="text-text truncate">{post.thread_title}</span>
          <span className="ml-auto text-text-muted tracking-[0.15em]">
            {post.intent?.toUpperCase() ?? "—"}
          </span>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span
        className="inline-block w-1.5 h-1.5 rounded-full"
        style={{ backgroundColor: color }}
      />
      {label}
    </span>
  );
}
