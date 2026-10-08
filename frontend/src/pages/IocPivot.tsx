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
import { QueryError } from "../components/Evidence";
import { iocColor as colorFor, INK, PAPER } from "../lib/palette";

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
      <div className="mx-auto max-w-[1440px] px-4 pt-12 sm:px-8">
        <p className="text-sm text-text-muted">
          No IOC in the address. Open one from a post's IOC list to pivot on it.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1840px] px-4 pb-24 sm:px-8">
      <SectionDivider
        index="05"
        label="IOC pivot"
        trailing={
          list.isLoading
            ? "Searching…"
            : `${postIds.length}${postIds.length === MAX_POSTS ? "+" : ""} posts · ${types.join(", ") || "—"}`
        }
      />

      <div className="mb-6 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h1 className="m-0 break-all font-mono text-2xl font-semibold tracking-normal">{value}</h1>
        <span className="text-sm text-text-muted">
          Every post that mentions this indicator. Shared indicators below lead to related posts.
        </span>
        <Link to="/posts" className="ml-auto text-sm font-semibold text-accent underline">
          Back to posts
        </Link>
      </div>
      {list.isError && <QueryError what="matching posts" error={list.error} onRetry={() => list.refetch()} />}

      {list.isLoading ? (
        <p className="text-sm text-text-muted">Searching the corpus…</p>
      ) : exact.length === 0 ? (
        <p className="border border-dashed border-border-soft p-8 text-sm text-text-muted">
          No post mentions this exact indicator.
        </p>
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
          <h3 className="m-0 mb-3 border-t-2 border-rule pt-2 text-sm font-bold">
            Indicators that appear alongside it
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {coIocs.map((c) => (
              <Link
                key={c.type + c.value}
                to={`/iocs/${encodeURIComponent(c.value)}`}
                className="border border-border-soft bg-surface-1 px-2 py-1 font-mono text-xs no-underline hover:border-text"
                title={`Appears in ${c.n} of these posts`}
              >
                <span
                  className="mr-1.5 inline-block h-2 w-2 align-middle"
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
    <div className="border border-text bg-surface-1">
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
              stroke="rgba(230,237,231,0.22)"
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
                fill={PAPER}
                stroke={INK}
                strokeWidth={2}
                
              />
              <rect x={-9} y={-9} width={18} height={18} fill={colorFor(types[0] ?? "")} />
              <text
                y={48}
                textAnchor="middle"
                fontFamily="Public Sans Variable, sans-serif"
                fontSize={11}
                fill="rgba(230,237,231,0.95)"
              >
                {truncate(c.label, 36)}
              </text>
              <text
                y={62}
                textAnchor="middle"
                fontFamily="Public Sans Variable, sans-serif"
                fontSize={9}
                fill="rgba(230,237,231,0.6)"
              >
                {types[0] ?? "—"}
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
              fill={INK}
              stroke="rgba(0,0,0,0.5)"
              strokeWidth={0.5}
            />
            <text
              x={12}
              y={4}
              fontFamily="Public Sans Variable, sans-serif"
              fontSize={10}
              fill="rgba(230,237,231,0.85)"
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
