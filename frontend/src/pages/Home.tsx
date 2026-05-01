import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { api } from "../lib/api";
import { useScramble } from "../hooks/useScramble";
import { SectionDivider } from "../components/Shell";

export function Home() {
  const stats = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  const health = useQuery({
    queryKey: ["healthz", "full"],
    queryFn: api.healthzFull,
    refetchInterval: 15_000,
  });

  return (
    <div className="max-w-[1440px] mx-auto px-8">
      <Hero
        posts={stats.data?.posts_total ?? 0}
        techniques={stats.data?.post_techniques_total ?? 0}
        iocs={stats.data?.iocs_total ?? 0}
        loading={stats.isLoading}
      />

      <SectionDivider
        index="02"
        label="System status"
        trailing={
          health.data ? `health: ${health.data.status.toUpperCase()}` : "checking…"
        }
      />
      <HealthGrid data={health.data} loading={health.isLoading} />

      <SectionDivider
        index="03"
        label="Intelligence feed"
        trailing={stats.data ? `${stats.data.posts_total} posts indexed` : ""}
      />
      <FeedTeaser />

      <SectionDivider index="04" label="Mitre activity" trailing="top techniques" />
      <TopTechniques data={stats.data?.top_techniques ?? []} />

      <div className="h-32" />
    </div>
  );
}

function Hero({
  posts,
  techniques,
  iocs,
  loading,
}: {
  posts: number;
  techniques: number;
  iocs: number;
  loading: boolean;
}) {
  const title = useScramble("OBSERVING THE DARK", []);
  return (
    <section className="hero-gradient relative pt-24 pb-20 px-2 -mx-8 mb-4">
      <div className="max-w-[1440px] mx-auto px-8 grid grid-cols-12 gap-6">
        <div className="col-span-12 md:col-span-8">
          <div className="font-mono text-xs tracking-[0.3em] text-accent mb-6">
            [01] // CONSOLE
          </div>
          <h1 className="font-display text-5xl md:text-7xl font-semibold leading-[1.05] tracking-tight">
            <span className="scramble">{title}</span>
          </h1>
          <p className="mt-6 max-w-2xl text-text-muted leading-relaxed">
            A continuous threat-intelligence pipeline. Scrapes a synthetic .onion
            forum over Tor, extracts IOCs and entities, runs a local LLM chain
            against every post, and maps behaviours to MITRE ATT&amp;CK. Built
            end-to-end as a learning project, every layer real.
          </p>
        </div>
        <div className="col-span-12 md:col-span-4 flex flex-col gap-4 justify-end">
          <Stat label="POSTS INDEXED" value={posts} loading={loading} />
          <Stat label="TECHNIQUE LINKS" value={techniques} loading={loading} />
          <Stat label="IOCS EXTRACTED" value={iocs} loading={loading} />
        </div>
      </div>
    </section>
  );
}

function Stat({
  label,
  value,
  loading,
}: {
  label: string;
  value: number;
  loading: boolean;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="border border-border-soft border-l-2 border-l-accent bg-surface-1/70 backdrop-blur-sm px-5 py-4 hover:border-accent/60 transition-colors"
      style={{ boxShadow: "inset 0 0 24px rgba(167,139,250,0.08)" }}
    >
      <div className="font-mono text-[10px] tracking-[0.2em] text-text-muted">
        {label}
      </div>
      <div className="font-display text-3xl mt-1 tabular-nums text-text">
        {loading ? "—" : value.toLocaleString()}
      </div>
    </motion.div>
  );
}

function HealthGrid({
  data,
  loading,
}: {
  data: import("../lib/api").HealthFull | undefined;
  loading: boolean;
}) {
  const ORDER: { key: string; tint: string }[] = [
    { key: "db", tint: "rgb(167,139,250)" },
    { key: "ollama", tint: "rgb(110,231,183)" },
    { key: "tor_socks", tint: "rgb(125,211,252)" },
    { key: "pipeline", tint: "rgb(232,163,61)" },
  ];
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {ORDER.map(({ key: k, tint }) => {
        const c = data?.checks[k];
        const up = c?.status === "up";
        return (
          <div
            key={k}
            className="border border-border-soft bg-surface-1/60 backdrop-blur-sm px-4 py-4 transition-colors hover:bg-surface-1/80"
            style={{
              borderLeft: `2px solid ${up ? tint : "rgba(229,72,77,0.7)"}`,
              boxShadow: up ? `inset 0 0 18px ${tint}1a` : undefined,
            }}
          >
            <div className="flex items-center gap-2 mb-2">
              <span
                className={`inline-block w-1.5 h-1.5 rounded-full ${
                  loading
                    ? "bg-text-muted animate-pulse"
                    : up
                    ? ""
                    : "bg-danger"
                }`}
                style={
                  up
                    ? {
                        backgroundColor: tint,
                        boxShadow: `0 0 6px ${tint}`,
                      }
                    : undefined
                }
              />
              <span className="font-mono text-[10px] tracking-[0.2em] text-text-muted uppercase">
                {k}
              </span>
            </div>
            <div
              className="font-mono text-sm"
              style={{ color: up ? tint : undefined }}
            >
              {loading ? "…" : up ? "ONLINE" : "DOWN"}
            </div>
            {c?.latency_ms !== undefined && (
              <div className="font-mono text-[10px] text-text-muted mt-1">
                {c.latency_ms} ms
              </div>
            )}
            {c?.error && (
              <div className="font-mono text-[10px] text-danger mt-1 truncate">
                {c.error}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function FeedTeaser() {
  const posts = useQuery({
    queryKey: ["posts", { limit: 6 }],
    queryFn: () => api.posts({ limit: 6 }),
  });
  if (posts.isLoading)
    return <div className="font-mono text-xs text-text-muted">loading feed…</div>;
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
      {posts.data?.items.map((p) => {
        const intentTint =
          p.intent === "sale"
            ? "border-l-warn"
            : p.intent === "doxxing"
            ? "border-l-danger"
            : p.intent === "recruitment"
            ? "border-l-[rgb(125,211,252)]"
            : "border-l-accent";
        return (
          <article
            key={p.id}
            className={`border border-border-soft border-l-2 ${intentTint} bg-surface-1/60 backdrop-blur-sm p-4 hover:border-accent/60 hover:bg-surface-1/80 transition-colors`}
          >
            <div className="flex items-center gap-2 font-mono text-[10px] tracking-[0.15em] text-text-muted">
              <span className="text-accent">#{p.id}</span>
              <span>{p.category.toUpperCase()}</span>
              {p.intent && (
                <span className="ml-auto text-warn">
                  {p.intent.toUpperCase()}
                </span>
              )}
            </div>
            <h3 className="font-display text-base mt-2 leading-tight text-text">
              {p.thread_title}
            </h3>
            <p className="mt-2 text-xs text-text-muted line-clamp-3">
              {p.summary || p.body_preview}
            </p>
          </article>
        );
      })}
    </div>
  );
}

function TopTechniques({
  data,
}: {
  data: { technique_id: string; name: string | null; n: number }[];
}) {
  if (!data.length)
    return <div className="font-mono text-xs text-text-muted">no data</div>;
  const max = Math.max(...data.map((d) => d.n));
  return (
    <div className="space-y-2">
      {data.slice(0, 10).map((t) => (
        <div
          key={t.technique_id}
          className="grid grid-cols-12 items-center gap-3 font-mono text-xs"
        >
          <span className="col-span-2 text-accent">{t.technique_id}</span>
          <span className="col-span-6 text-text truncate">{t.name ?? "—"}</span>
          <div className="col-span-3 h-1.5 bg-surface-2/60 relative overflow-hidden">
            <div
              className="absolute left-0 top-0 bottom-0"
              style={{
                width: `${(t.n / max) * 100}%`,
                background:
                  "linear-gradient(to right, rgba(167,139,250,0.5), rgb(167,139,250))",
                boxShadow: "0 0 8px rgba(167,139,250,0.6)",
              }}
            />
          </div>
          <span className="col-span-1 text-right text-text-muted tabular-nums">
            {t.n}
          </span>
        </div>
      ))}
    </div>
  );
}
