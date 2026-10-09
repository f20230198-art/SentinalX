import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import type { PostDetail } from "../lib/api";
import { INK, MUTED, PAPER, iocColor } from "../lib/palette";

/* Force graph of posts, IOCs and techniques; shared nodes pull related posts together.
 * Hover = highlight, click = open/pin, double-click IOC = pivot, drag = move. */

// Three node types: post, IOC, MITRE technique
type Kind = "post" | "ioc" | "mitre";

// One dot in the graph (d3 adds x/y positions to it)
interface GNode extends SimulationNodeDatum {
  id: string;
  kind: Kind;
  label: string;
  postId?: number;
  iocType?: string;
  iocValue?: string;
  techniqueName?: string;
  degree: number;
}
// A line between a post and one of its IOCs/techniques
type GLink = SimulationLinkDatum<GNode> & { source: string | GNode; target: string | GNode };

export function EvidenceGraph({
  posts,
  onSelectPost,
  height = 640,
  focusTechnique,
}: {
  posts: PostDetail[];
  onSelectPost: (id: number) => void;
  height?: number;
  /** Pre-pin a technique cluster (e.g. the Home page's featured finding). */
  focusTechnique?: string;
}) {
  // Drawing area size
  const W = 1200;
  const H = height;
  const navigate = useNavigate();
  const svgRef = useRef<SVGSVGElement | null>(null);
  const simRef = useRef<Simulation<GNode, GLink> | null>(null);
  // Changing `tick` re-renders the graph as d3 moves the nodes
  const [, setTick] = useState(0);
  // Node under the mouse, and node clicked to stay highlighted
  const [hover, setHover] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(focusTechnique ? `mitre:${focusTechnique}` : null);

  // Build nodes and links from the posts; the same IOC/technique is one shared node
  const { nodes, links, adj } = useMemo(() => {
    const ns = new Map<string, GNode>();
    const ls: GLink[] = [];
    // Add a node, or if it already exists just count one more link to it
    const add = (n: Omit<GNode, "degree">) => {
      const ex = ns.get(n.id);
      if (ex) {
        ex.degree++;
        return;
      }
      ns.set(n.id, { ...n, degree: 1 });
    };
    // For each post: a post node, plus links to its IOCs and techniques
    for (const p of posts) {
      const pid = `post:${p.post.id}`;
      add({ id: pid, kind: "post", postId: p.post.id, label: p.post.thread_title || `#${p.post.id}` });
      for (const i of p.iocs) {
        const k = `ioc:${i.ioc_type}::${i.value}`;
        add({ id: k, kind: "ioc", label: i.value, iocType: i.ioc_type, iocValue: i.value });
        ls.push({ source: pid, target: k });
      }
      for (const t of p.techniques) {
        const k = `mitre:${t.technique_id}`;
        if (ls.some((l) => l.source === pid && l.target === k)) continue; // one edge per post/technique
        add({ id: k, kind: "mitre", label: t.technique_id, techniqueName: t.name ?? "" });
        ls.push({ source: pid, target: k });
      }
    }
    // Neighbour list for each node (used for highlighting)
    const a = new Map<string, Set<string>>();
    for (const l of ls) {
      const s = String(l.source), t = String(l.target);
      if (!a.has(s)) a.set(s, new Set());
      if (!a.has(t)) a.set(t, new Set());
      a.get(s)!.add(t);
      a.get(t)!.add(s);
    }
    return { nodes: [...ns.values()], links: ls, adj: a };
  }, [posts]);

  // Start the physics: nodes repel, links pull, everything drifts to the centre
  useEffect(() => {
    if (!nodes.length) return;
    const sim = forceSimulation<GNode>(nodes)
      .force("center", forceCenter(W / 2, H / 2))
      .force("x", forceX<GNode>(W / 2).strength(0.04))
      .force("y", forceY<GNode>(H / 2).strength(0.06))
      .force("charge", forceManyBody<GNode>().strength((d) => (d.kind === "post" ? -260 : -90 - d.degree * 20)))
      .force(
        "link",
        forceLink<GNode, GLink>(links)
          .id((d) => d.id)
          .distance((l) => ((l.target as GNode).kind === "mitre" ? 140 : 80))
          .strength(0.45),
      )
      .force("collide", forceCollide<GNode>().radius((d) => (d.kind === "post" ? 32 : 14 + Math.min(d.degree, 6) * 2)))
      .alphaDecay(0.03)
      .on("tick", () => setTick((t) => t + 1));
    simRef.current = sim;
    return () => void sim.stop();
  }, [nodes, links, H]);

  // Esc un-pins the highlighted node
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setPinned(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ---- drag (pointer events; converts screen → SVG coordinates) ---------
  const drag = useRef<{ node: GNode; moved: boolean } | null>(null);
  // Mouse position -> graph coordinates
  const toSvg = (e: React.PointerEvent) => {
    const svg = svgRef.current!;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    return pt.matrixTransform(svg.getScreenCTM()!.inverse());
  };
  // Start dragging: wake up the physics
  const onDown = (n: GNode) => (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture(e.pointerId);
    drag.current = { node: n, moved: false };
    simRef.current?.alphaTarget(0.25).restart();
  };
  // While dragging: pin the node to the mouse
  const onMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    const p = toSvg(e);
    drag.current.moved = true;
    drag.current.node.fx = p.x;
    drag.current.node.fy = p.y;
  };
  const onUp = () => {
    simRef.current?.alphaTarget(0);
    // keep fx/fy: a dragged node stays where it was dropped
    setTimeout(() => (drag.current = null), 0);
  };

  // Click: a post opens its details; anything else pins/unpins its cluster
  const activate = (n: GNode) => {
    if (drag.current?.moved) return;
    if (n.kind === "post" && n.postId !== undefined) onSelectPost(n.postId);
    else setPinned((p) => (p === n.id ? null : n.id));
  };

  // Highlight the hovered (or pinned) node and its neighbours; dim everything else
  const focus = hover ?? pinned;
  const lit = (id: string) => !focus || id === focus || (adj.get(focus)?.has(id) ?? false);
  const pinnedNode = pinned ? nodes.find((n) => n.id === pinned) : undefined;

  // Nothing to draw
  if (!nodes.length) {
    return <p className="border border-dashed border-border-soft p-8 text-sm text-text-muted">No evidence to draw yet.</p>;
  }

  return (
    <figure className="m-0">
      <div className="relative border border-text bg-surface-1">
        {/* Info box for the pinned node */}
        {pinnedNode && (
          <div className="absolute top-3 left-3 z-10 max-w-sm border border-text bg-surface-1 px-3 py-2 text-sm shadow-[0_4px_16px_rgba(0,0,0,0.45)]">
            <span className="font-mono font-semibold">{pinnedNode.label}</span>
            {pinnedNode.techniqueName && <span className="ml-2">{pinnedNode.techniqueName}</span>}
            {pinnedNode.iocType && <span className="ml-2 text-text-muted">{pinnedNode.iocType}</span>}
            <span className="ml-2 text-text-muted tabular-nums">· {adj.get(pinnedNode.id)?.size ?? 0} posts</span>
            <div className="mt-1 flex gap-3 text-xs">
              {pinnedNode.kind === "ioc" && (
                <button className="font-semibold text-accent underline" onClick={() => navigate(`/iocs/${encodeURIComponent(pinnedNode.iocValue!)}`)}>
                  Pivot on this indicator
                </button>
              )}
              <button className="text-text-muted underline" onClick={() => setPinned(null)}>
                Release (Esc)
              </button>
            </div>
          </div>
        )}
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          className="block h-auto w-full touch-none select-none"
          onPointerMove={onMove}
          onPointerUp={onUp}
          role="group"
          aria-label="Evidence graph of posts, indicators and ATT&CK techniques"
        >
          {/* Lines first, so dots are drawn on top */}
          {links.map((l, i) => {
            const s = l.source as GNode, t = l.target as GNode;
            if (typeof s !== "object") return null;
            const on = !focus || (lit(s.id) && lit(t.id) && (s.id === focus || t.id === focus));
            return (
              <line
                key={i}
                x1={s.x} y1={s.y} x2={t.x} y2={t.y}
                stroke={t.kind === "mitre" ? INK : iocColor(t.iocType ?? "")}
                strokeOpacity={on ? (focus ? 0.7 : 0.22) : 0.05}
                strokeWidth={on && focus ? 1.6 : 1}
              />
            );
          })}

          {/* Nodes: posts = circles, IOCs = diamonds, techniques = boxes */}
          {nodes.map((n) => {
            const on = lit(n.id);
            // Mouse/keyboard handlers shared by every node
            const common = {
              transform: `translate(${n.x ?? 0},${n.y ?? 0})`,
              style: { cursor: "pointer", opacity: on ? 1 : 0.15, transition: "opacity 160ms" } as React.CSSProperties,
              tabIndex: 0,
              role: "button",
              onPointerDown: onDown(n),
              onClick: () => activate(n),
              onDoubleClick: () => n.kind === "ioc" && navigate(`/iocs/${encodeURIComponent(n.iocValue!)}`),
              onKeyDown: (e: React.KeyboardEvent) => e.key === "Enter" && activate(n),
              onMouseEnter: () => setHover(n.id),
              onMouseLeave: () => setHover(null),
              onFocus: () => setHover(n.id),
              onBlur: () => setHover(null),
            };
            // Full title only for the hovered post; neighbours just show "#id"
            const isFocus = n.id === focus;
            const showLabel = n.kind === "post" ? on && (!!focus || nodes.length < 40) : on && (n.degree > 1 || isFocus);
            if (n.kind === "post")
              return (
                <g key={n.id} {...common} aria-label={`Post ${n.postId}: ${n.label}`}>
                  <circle r={9} fill={INK} stroke={PAPER} strokeWidth={2} />
                  {showLabel && (
                    <text x={14} y={4} fontSize={12} fontWeight={600} fill={INK} paintOrder="stroke" stroke={PAPER} strokeWidth={4}>
                      #{n.postId}{(isFocus || (!focus && nodes.length < 15)) && ` ${trunc(n.label, 40)}`}
                    </text>
                  )}
                </g>
              );
            if (n.kind === "ioc") {
              const r = 5 + Math.min(n.degree, 6) * 1.2;
              return (
                <g key={n.id} {...common} aria-label={`${n.iocType} ${n.label}, in ${n.degree} posts`}>
                  <rect x={-r} y={-r} width={r * 2} height={r * 2} transform="rotate(45)" fill={iocColor(n.iocType ?? "")} />
                  {showLabel && (
                    <text x={r + 6} y={4} fontSize={11} fontFamily="JetBrains Mono Variable, monospace" fill={MUTED} paintOrder="stroke" stroke={PAPER} strokeWidth={4}>
                      {trunc(n.label, 26)}
                    </text>
                  )}
                </g>
              );
            }
            const s = 12 + Math.min(n.degree, 8) * 1.5;
            const isPinned = n.id === pinned;
            return (
              <g key={n.id} {...common} aria-label={`Technique ${n.label} ${n.techniqueName}, in ${n.degree} posts`}>
                <rect x={-s} y={-s / 1.6} width={s * 2} height={s * 1.25} fill={isPinned ? INK : PAPER} stroke={INK} strokeWidth={2} />
                <text textAnchor="middle" y={4} fontSize={11} fontWeight={700} fontFamily="JetBrains Mono Variable, monospace" fill={isPinned ? PAPER : INK}>
                  {n.label}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      {/* Legend */}
      <figcaption className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-text-muted">
        <span><span className="mr-1.5 inline-block h-3 w-3 rounded-full bg-text align-middle" />Post</span>
        <span><span className="mr-1.5 inline-block h-2.5 w-2.5 rotate-45 bg-info align-middle" />Indicator (colour = type)</span>
        <span><span className="mr-1.5 inline-block h-3 w-4 border-2 border-text bg-surface-1 align-middle" />ATT&amp;CK technique</span>
        <span className="ml-auto">Hover to trace · click a technique or indicator to pin its cluster · drag to arrange</span>
      </figcaption>
    </figure>
  );
}

// Shorten long labels with "…"
const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
