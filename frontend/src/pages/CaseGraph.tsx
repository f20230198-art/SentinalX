import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { api, type PostDetail } from "../lib/api";
import { SectionDivider } from "../components/Shell";
import { DetailPanel } from "../components/DetailPanel";

/* ----------------------------------------------------------------------- *
 * Case-file graph.
 *
 * Route: /investigations/:id/graph
 *
 * For a given investigation, render a single d3-force canvas containing:
 *   - one POST node per matched post (cap MAX_POSTS for legibility),
 *   - one IOC node per unique (ioc_type, value) seen across those posts,
 *   - one MITRE node per unique technique_id seen across those posts.
 * Edges: post→ioc, post→technique. Shared IOCs/techniques pull their posts
 * into clusters automatically — that's the whole point of the view.
 *
 * IocPivot.tsx is the structural reference; this is the bigger-picture
 * overlay it complements.
 * ----------------------------------------------------------------------- */

const MAX_POSTS = 25;
const VIEW_W = 1200;
const VIEW_H = 700;

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
const colorForIoc = (t: string) => TYPE_COLOR[t] ?? "rgb(167, 139, 250)";

type NodeKind = "post" | "ioc" | "mitre";

interface CaseNode extends SimulationNodeDatum {
  id: string;
  kind: NodeKind;
  label: string;
  // post-only
  postId?: number;
  intent?: string | null;
  // ioc-only
  iocType?: string;
  iocValue?: string;
  // mitre-only
  techniqueId?: string;
  techniqueName?: string;
  // shared
  degree?: number;
}

export function CaseGraph() {
  const { id } = useParams<{ id: string }>();
  const investigationId = id ? Number(id) : NaN;
  const [selectedPost, setSelectedPost] = useState<number | null>(null);
  const [hoverNode, setHoverNode] = useState<string | null>(null);

  const inv = useQuery({
    queryKey: ["investigation", investigationId],
    queryFn: () => api.investigation(investigationId),
    enabled: Number.isFinite(investigationId),
  });

  const matchedIds = useMemo(
    () =>
      (inv.data?.matched_posts ?? [])
        .slice(0, MAX_POSTS)
        .map((p) => p.id),
    [inv.data],
  );

  const postsQ = useQueries({
    queries: matchedIds.map((pid) => ({
      queryKey: ["post", pid],
      queryFn: () => api.post(pid),
    })),
  });

  const posts = useMemo(
    () =>
      postsQ
        .map((q) => q.data)
        .filter((p): p is PostDetail => Boolean(p)),
    [postsQ],
  );

  const loadingPosts = postsQ.some((q) => q.isLoading);

  if (!Number.isFinite(investigationId)) {
    return (
      <div className="max-w-[1440px] mx-auto px-8 pt-12">
        <div className="font-mono text-sm text-text-muted">
          missing investigation id.
        </div>
      </div>
    );
  }

  const totalMatched = inv.data?.matched_total ?? 0;
  const truncated = totalMatched > MAX_POSTS;

  return (
    <div className="max-w-[1840px] mx-auto px-8">
      <SectionDivider
        index="06"
        label="Case graph"
        trailing={
          inv.isLoading
            ? "loading…"
            : `${matchedIds.length}${truncated ? "+" : ""} posts · ${posts.length}/${matchedIds.length} loaded`
        }
      />

      <div className="mb-3 flex items-baseline gap-3 font-mono">
        <span className="text-[10px] tracking-[0.2em] text-text-muted">
          INVESTIGATION
        </span>
        <span className="text-accent text-base">
          #{inv.data?.id} {inv.data?.name}
        </span>
        {inv.data?.lens && (
          <span className="text-[10px] tracking-[0.18em] text-accent/80">
            · {inv.data.lens.toUpperCase()}
          </span>
        )}
        <Link
          to="/investigations"
          className="ml-auto text-[10px] text-text-muted hover:text-accent"
        >
          ← back to investigations
        </Link>
      </div>

      {inv.isLoading ? (
        <div className="font-mono text-xs text-text-muted">
          loading investigation…
        </div>
      ) : matchedIds.length === 0 ? (
        <div className="border border-dashed border-border-soft p-8 font-mono text-xs text-text-muted">
          this investigation has no matched posts yet.
        </div>
      ) : loadingPosts ? (
        <div className="border border-dashed border-border-soft p-8 font-mono text-xs text-text-muted">
          fetching {matchedIds.length} post details…
        </div>
      ) : (
        <CaseGraphCanvas
          posts={posts}
          onSelectPost={setSelectedPost}
          hoverNode={hoverNode}
          setHoverNode={setHoverNode}
        />
      )}

      <Legend />

      <DetailPanel
        id={selectedPost}
        onClose={() => setSelectedPost(null)}
      />

      <div className="h-32" />
    </div>
  );
}

function Legend() {
  return (
    <div className="mt-4 flex flex-wrap gap-4 font-mono text-[10px] text-text-muted">
      <span>
        <span className="inline-block w-3 h-3 rounded-full bg-accent mr-1.5 align-middle" />
        post
      </span>
      <span>
        <span
          className="inline-block w-3 h-3 mr-1.5 align-middle"
          style={{
            backgroundColor: "rgb(125, 211, 252)",
            transform: "rotate(45deg)",
          }}
        />
        ioc (color = type)
      </span>
      <span>
        <span
          className="inline-block w-3 h-3 mr-1.5 align-middle border border-warn"
          style={{ backgroundColor: "rgba(232,163,61,0.25)" }}
        />
        MITRE technique
      </span>
      <span className="ml-auto opacity-60">click post to inspect · click IOC to pivot</span>
    </div>
  );
}

function CaseGraphCanvas({
  posts,
  onSelectPost,
  hoverNode,
  setHoverNode,
}: {
  posts: PostDetail[];
  onSelectPost: (id: number) => void;
  hoverNode: string | null;
  setHoverNode: (id: string | null) => void;
}) {
  const [, setTick] = useState(0);
  const simRef = useRef<Simulation<
    CaseNode,
    SimulationLinkDatum<CaseNode>
  > | null>(null);

  const { nodes, links, edgeIndex } = useMemo(() => {
    const ns = new Map<string, CaseNode>();
    const ls: SimulationLinkDatum<CaseNode>[] = [];

    for (const p of posts) {
      const pid = `post:${p.post.id}`;
      ns.set(pid, {
        id: pid,
        kind: "post",
        postId: p.post.id,
        label: p.post.thread_title || `#${p.post.id}`,
        intent: p.analysis?.intent ?? null,
      });
    }

    for (const p of posts) {
      const pid = `post:${p.post.id}`;

      for (const i of p.iocs) {
        const key = `ioc:${i.ioc_type}::${i.value}`;
        let n = ns.get(key);
        if (!n) {
          n = {
            id: key,
            kind: "ioc",
            label: i.value,
            iocType: i.ioc_type,
            iocValue: i.value,
            degree: 0,
          };
          ns.set(key, n);
        }
        n.degree = (n.degree ?? 0) + 1;
        ls.push({ source: pid, target: key });
      }

      for (const t of p.techniques) {
        const key = `mitre:${t.technique_id}`;
        let n = ns.get(key);
        if (!n) {
          n = {
            id: key,
            kind: "mitre",
            label: t.technique_id,
            techniqueId: t.technique_id,
            techniqueName: t.name ?? "",
            degree: 0,
          };
          ns.set(key, n);
        }
        n.degree = (n.degree ?? 0) + 1;
        ls.push({ source: pid, target: key });
      }
    }

    // For hover highlighting: precompute neighbour set per node id.
    const idx = new Map<string, Set<string>>();
    for (const l of ls) {
      const s = String(l.source);
      const t = String(l.target);
      if (!idx.has(s)) idx.set(s, new Set());
      if (!idx.has(t)) idx.set(t, new Set());
      idx.get(s)!.add(t);
      idx.get(t)!.add(s);
    }

    return {
      nodes: [...ns.values()],
      links: ls,
      edgeIndex: idx,
    };
  }, [posts]);

  useEffect(() => {
    if (nodes.length === 0) return;
    const sim = forceSimulation<CaseNode>(nodes)
      .force("center", forceCenter(VIEW_W / 2, VIEW_H / 2))
      .force(
        "charge",
        forceManyBody<CaseNode>().strength((d) =>
          d.kind === "post" ? -240 : -120,
        ),
      )
      .force(
        "link",
        forceLink<CaseNode, SimulationLinkDatum<CaseNode>>(links)
          .id((d) => (d as CaseNode).id)
          .distance((l) => {
            const t = (l.target as CaseNode).kind;
            return t === "ioc" ? 70 : 90;
          })
          .strength(0.5),
      )
      .force(
        "collide",
        forceCollide<CaseNode>().radius((d) =>
          d.kind === "post" ? 22 : 14,
        ),
      )
      .alpha(1)
      .alphaDecay(0.035)
      .on("tick", () => setTick((t) => t + 1));

    simRef.current = sim;
    return () => {
      sim.stop();
    };
  }, [nodes, links]);

  const isHighlighted = (nid: string): boolean => {
    if (!hoverNode) return true;
    if (hoverNode === nid) return true;
    return edgeIndex.get(hoverNode)?.has(nid) ?? false;
  };
  const dim = (nid: string): number =>
    hoverNode ? (isHighlighted(nid) ? 1 : 0.18) : 1;

  return (
    <div className="border border-border-soft bg-surface-1/30 backdrop-blur-sm">
      <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} className="w-full h-auto">
        {/* Edges */}
        {links.map((l, i) => {
          const s = l.source as CaseNode;
          const t = l.target as CaseNode;
          if (typeof s !== "object" || typeof t !== "object") return null;
          const active =
            !hoverNode ||
            hoverNode === s.id ||
            hoverNode === t.id;
          return (
            <line
              key={i}
              x1={s.x}
              y1={s.y}
              x2={t.x}
              y2={t.y}
              stroke={
                t.kind === "ioc"
                  ? "rgba(125,211,252,0.32)"
                  : t.kind === "mitre"
                    ? "rgba(232,163,61,0.32)"
                    : "rgba(167,139,250,0.25)"
              }
              strokeWidth={active ? 1.2 : 0.6}
              opacity={active ? 1 : 0.2}
            />
          );
        })}

        {/* Nodes */}
        {nodes.map((n) => {
          const x = n.x ?? 0;
          const y = n.y ?? 0;
          const op = dim(n.id);
          if (n.kind === "post") {
            return (
              <g
                key={n.id}
                transform={`translate(${x},${y})`}
                style={{ cursor: "pointer", opacity: op }}
                onClick={() =>
                  n.postId !== undefined && onSelectPost(n.postId)
                }
                onMouseEnter={() => setHoverNode(n.id)}
                onMouseLeave={() => setHoverNode(null)}
              >
                <circle
                  r={11}
                  fill="rgb(167, 139, 250)"
                  stroke="rgba(255,255,255,0.5)"
                  strokeWidth={0.8}
                  style={{
                    filter:
                      hoverNode === n.id
                        ? "drop-shadow(0 0 10px rgba(167,139,250,0.9))"
                        : undefined,
                  }}
                />
                <text
                  x={15}
                  y={4}
                  fontFamily="JetBrains Mono, monospace"
                  fontSize={10}
                  fill="rgba(230,227,240,0.9)"
                >
                  #{n.postId} {truncate(n.label, 28)}
                </text>
              </g>
            );
          }
          if (n.kind === "ioc") {
            // Diamond (rotated square).
            return (
              <Link
                key={n.id}
                to={`/iocs/${encodeURIComponent(n.iocValue ?? "")}`}
              >
                <g
                  transform={`translate(${x},${y})`}
                  style={{ cursor: "pointer", opacity: op }}
                  onMouseEnter={() => setHoverNode(n.id)}
                  onMouseLeave={() => setHoverNode(null)}
                >
                  <rect
                    x={-7}
                    y={-7}
                    width={14}
                    height={14}
                    transform="rotate(45)"
                    fill={colorForIoc(n.iocType ?? "")}
                    fillOpacity={0.55}
                    stroke={colorForIoc(n.iocType ?? "")}
                    strokeWidth={1}
                  />
                  <text
                    x={11}
                    y={4}
                    fontFamily="JetBrains Mono, monospace"
                    fontSize={9}
                    fill="rgba(230,227,240,0.78)"
                  >
                    {truncate(n.label, 22)}
                  </text>
                </g>
              </Link>
            );
          }
          // mitre square
          return (
            <g
              key={n.id}
              transform={`translate(${x},${y})`}
              style={{ cursor: "default", opacity: op }}
              onMouseEnter={() => setHoverNode(n.id)}
              onMouseLeave={() => setHoverNode(null)}
            >
              <rect
                x={-9}
                y={-9}
                width={18}
                height={18}
                fill="rgba(232,163,61,0.18)"
                stroke="rgb(232,163,61)"
                strokeWidth={1}
              />
              <text
                x={0}
                y={3}
                textAnchor="middle"
                fontFamily="JetBrains Mono, monospace"
                fontSize={8}
                fill="rgb(232,163,61)"
                fontWeight={600}
              >
                {n.techniqueId}
              </text>
              {hoverNode === n.id && n.techniqueName && (
                <text
                  x={0}
                  y={28}
                  textAnchor="middle"
                  fontFamily="JetBrains Mono, monospace"
                  fontSize={9}
                  fill="rgba(230,227,240,0.85)"
                >
                  {truncate(n.techniqueName, 36)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function truncate(s: string, n: number) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
