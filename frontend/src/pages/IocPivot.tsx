import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type Simulation,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from "d3-force";
import { api, type PostDetail } from "../lib/api";
import { SectionDivider } from "../components/Shell";
import { DetailPanel } from "../components/DetailPanel";

/* ----------------------------------------------------------------------- *
 * IOC pivot graph.
 *
 * Route: /iocs/:value (URL-encoded). Shows the chosen IOC as a central node
 * with one satellite per post that mentions it. Edges link the central IOC
 * to its posts; co-occurring IOCs across those posts are listed below as
 * clickable pivots so the analyst can chain through the corpus.
 *
 * d3-force runs on the client only — we never persist positions. The
 * simulation is small (≤30 posts), so a couple hundred ticks settles fast.
 * ----------------------------------------------------------------------- */

const MAX_POSTS = 30;
const VIEW_W = 1100;
const VIEW_H = 540;

const TYPE_COLOR: Record<string, string> = {
  ipv4: "rgb(125, 211, 252)",
  ipv6: "rgb(125, 211, 252)",
  domain: "rgb(167, 139, 250)",
  url: "rgb(167, 139, 250)",
  email: "rgb(196, 181, 253)",
  btc: "rgb(232, 163, 61)",
  cve: "rgb(229, 72, 77)",
  md5: "rgb(110, 231, 183)",
  sha1: "rgb(110, 231, 183)",
  sha256: "rgb(110, 231, 183)",
};
const colorFor = (t: string) => TYPE_COLOR[t] ?? "rgb(167, 139, 250)";

interface PivotNode extends SimulationNodeDatum {
  id: string;
  kind: "ioc" | "post";
  label: string;
  postId?: number;
  iocType?: string;
}

export function IocPivot() {
  const { value: rawValue } = useParams<{ value: string }>();
  const value = rawValue ? decodeURIComponent(rawValue) : "";
  const [selectedPost, setSelectedPost] = useState<number | null>(null);

  // /iocs uses LIKE matching, so we filter back down to exact value/type.
  const list = useQuery({
    queryKey: ["iocs", value],
    queryFn: () => api.iocs({ value, limit: 200 }),
    enabled: value.length > 0,
  });

  const exact = useMemo(
    () => list.data?.items.filter((i) => i.value === value) ?? [],
    [list.data, value],
  );

  // Aggregate post_ids across (potentially) multiple ioc_type rows for the
  // same value (e.g. someone tags the same string as both domain + url).
  const { postIds, types } = useMemo(() => {
    const ids = new Set<number>();
    const ts = new Set<string>();
    for (const r of exact) {
      ts.add(r.ioc_type);
      for (const id of r.post_ids) ids.add(id);
    }
    const all = [...ids];
    return {
      postIds: all.slice(0, MAX_POSTS),
      types: [...ts],
      total: all.length,
    };
  }, [exact]);

  const postsQ = useQueries({
    queries: postIds.map((id) => ({
      queryKey: ["post", id],
      queryFn: () => api.post(id),
    })),
  });

  const posts = useMemo(
    () =>
      postsQ
        .map((q) => q.data)
        .filter((p): p is PostDetail => Boolean(p)),
    [postsQ],
  );

  const coIocs = useMemo(() => {
    const m = new Map<string, { type: string; value: string; n: number }>();
    for (const p of posts) {
      for (const i of p.iocs) {
        if (i.value === value) continue;
        const key = `${i.ioc_type}::${i.value}`;
        const prev = m.get(key);
        if (prev) prev.n++;
        else m.set(key, { type: i.ioc_type, value: i.value, n: 1 });
      }
    }
    return [...m.values()].sort((a, b) => b.n - a.n).slice(0, 24);
  }, [posts, value]);

  if (!value) {
    return (
      <div className="max-w-[1440px] mx-auto px-8 pt-12">
        <div className="font-mono text-sm text-text-muted">
          missing IOC value in URL.
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-[1840px] mx-auto px-8">
      <SectionDivider
        index="05"
        label="IOC pivot"
        trailing={
          list.isLoading
            ? "loading…"
            : `${postIds.length}${postIds.length === MAX_POSTS ? "+" : ""} posts · ${types.join(", ") || "—"}`
        }
      />

      <div className="mb-3 flex items-baseline gap-3 font-mono">
        <span className="text-[10px] tracking-[0.2em] text-text-muted">
          PIVOT ON
        </span>
        <span className="text-accent text-base break-all">{value}</span>
        <span className="ml-auto text-[10px] text-text-muted">
          <Link to="/posts" className="hover:text-accent">
            ← back to feed
          </Link>
        </span>
      </div>

      {list.isLoading ? (
        <div className="font-mono text-xs text-text-muted">
          searching corpus…
        </div>
      ) : exact.length === 0 ? (
        <div className="border border-dashed border-border-soft p-8 font-mono text-xs text-text-muted">
          no posts mention this IOC.
        </div>
      ) : (
        <PivotGraph
          value={value}
          types={types}
          posts={posts}
          allPostIds={postIds}
          onSelect={setSelectedPost}
        />
      )}

      {coIocs.length > 0 && (
        <section className="mt-8">
          <div className="font-mono text-[10px] tracking-[0.2em] text-text-muted mb-2">
            CO-OCCURRING IOCS ACROSS THESE POSTS
          </div>
          <div className="flex flex-wrap gap-1.5">
            {coIocs.map((c) => (
              <Link
                key={c.type + c.value}
                to={`/iocs/${encodeURIComponent(c.value)}`}
                className="font-mono text-[11px] border border-border-soft px-2 py-1 hover:border-accent hover:text-accent transition-colors"
                title={`appears in ${c.n} of these posts`}
              >
                <span
                  className="inline-block w-1.5 h-1.5 rounded-full mr-1.5 align-middle"
                  style={{ backgroundColor: colorFor(c.type) }}
                />
                <span className="text-text-muted">{c.type}</span>{" "}
                <span className="text-text">{c.value}</span>
                <span className="ml-1.5 text-text-muted opacity-70">
                  ×{c.n}
                </span>
              </Link>
            ))}
          </div>
        </section>
      )}

      <DetailPanel id={selectedPost} onClose={() => setSelectedPost(null)} />

      <div className="h-32" />
    </div>
  );
}

function PivotGraph({
  value,
  types,
  posts,
  allPostIds,
  onSelect,
}: {
  value: string;
  types: string[];
  posts: PostDetail[];
  allPostIds: number[];
  onSelect: (id: number) => void;
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [tick, setTick] = useState(0);
  const simRef = useRef<Simulation<PivotNode, SimulationLinkDatum<PivotNode>> | null>(
    null,
  );

  // Build node + link arrays. Posts that haven't loaded yet still appear as
  // placeholder nodes so the graph stays stable as detail queries resolve.
  const { nodes, links } = useMemo(() => {
    const ns: PivotNode[] = [
      { id: "ioc:center", kind: "ioc", label: value, x: VIEW_W / 2, y: VIEW_H / 2, fx: VIEW_W / 2, fy: VIEW_H / 2 },
    ];
    const postById = new Map(posts.map((p) => [p.post.id, p]));
    for (const id of allPostIds) {
      const p = postById.get(id);
      ns.push({
        id: `post:${id}`,
        kind: "post",
        postId: id,
        label: p?.post.thread_title ?? `#${id}`,
        iocType: p?.analysis?.intent ?? undefined,
      });
    }
    const ls: SimulationLinkDatum<PivotNode>[] = ns
      .filter((n) => n.kind === "post")
      .map((n) => ({ source: "ioc:center", target: n.id }));
    return { nodes: ns, links: ls };
  }, [value, posts, allPostIds]);

  useEffect(() => {
    const sim = forceSimulation<PivotNode>(nodes)
      .force("center", forceCenter(VIEW_W / 2, VIEW_H / 2))
      .force("charge", forceManyBody().strength(-180))
      .force(
        "link",
        forceLink<PivotNode, SimulationLinkDatum<PivotNode>>(links)
          .id((d) => (d as PivotNode).id)
          .distance(160)
          .strength(0.6),
      )
      .force("collide", forceCollide<PivotNode>().radius(28))
      .alpha(1)
      .alphaDecay(0.04)
      .on("tick", () => setTick((t) => t + 1));

    simRef.current = sim;
    return () => {
      sim.stop();
    };
  }, [nodes, links]);

  // Reference tick to keep React aware of force ticks.
  void tick;

  return (
    <div className="border border-border-soft bg-surface-1/30 backdrop-blur-sm">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        className="w-full h-auto"
      >
        {/* Edges */}
        {links.map((l, i) => {
          const s = l.source as PivotNode;
          const t = l.target as PivotNode;
          if (typeof s !== "object" || typeof t !== "object") return null;
          return (
            <line
              key={i}
              x1={s.x}
              y1={s.y}
              x2={t.x}
              y2={t.y}
              stroke="rgba(167, 139, 250, 0.25)"
              strokeWidth={1}
            />
          );
        })}

        {/* Center IOC */}
        {(() => {
          const c = nodes[0];
          return (
            <g transform={`translate(${c.x},${c.y})`}>
              <circle
                r={26}
                fill="rgba(167, 139, 250, 0.15)"
                stroke="rgb(167, 139, 250)"
                strokeWidth={1.5}
                style={{ filter: "drop-shadow(0 0 14px rgba(167,139,250,0.5))" }}
              />
              <circle r={10} fill={colorFor(types[0] ?? "")} />
              <text
                y={48}
                textAnchor="middle"
                fontFamily="JetBrains Mono, monospace"
                fontSize={11}
                fill="rgba(230,227,240,0.95)"
              >
                {truncate(c.label, 36)}
              </text>
              <text
                y={62}
                textAnchor="middle"
                fontFamily="JetBrains Mono, monospace"
                fontSize={9}
                fill="rgba(230,227,240,0.55)"
                letterSpacing="0.18em"
              >
                {(types[0] ?? "—").toUpperCase()}
              </text>
            </g>
          );
        })()}

        {/* Post satellites */}
        {nodes.slice(1).map((n) => (
          <g
            key={n.id}
            transform={`translate(${n.x ?? 0},${n.y ?? 0})`}
            style={{ cursor: "pointer" }}
            onClick={() => n.postId !== undefined && onSelect(n.postId)}
          >
            <circle
              r={8}
              fill="rgb(167, 139, 250)"
              stroke="rgba(255,255,255,0.4)"
              strokeWidth={0.5}
            />
            <text
              x={12}
              y={4}
              fontFamily="JetBrains Mono, monospace"
              fontSize={10}
              fill="rgba(230,227,240,0.85)"
            >
              #{n.postId} {truncate(n.label, 28)}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function truncate(s: string, n: number) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
