import { lazy, Suspense, useState } from "react";
import { Link } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type HealthFull, type PostDetail, type Stats } from "../lib/api";
import { SectionDivider } from "../components/Shell";
import { DetailPanel } from "../components/DetailPanel";
import {
  ProvenanceBar,
  ProvenanceLegend,
  QueryError,
} from "../components/Evidence";
import { intentColor } from "../lib/palette";

// d3-force only loads when the evidence web scrolls into the page.
const EvidenceGraph = lazy(() =>
  import("../components/EvidenceGraph").then((m) => ({ default: m.EvidenceGraph })),
);

// Number with thousands separators ("—" while loading)
const fmt = (n: number | undefined) => (n === undefined ? "—" : n.toLocaleString());

// Overview page: top finding, evidence graph, system status, latest posts, top techniques
export function Home() {
  const { t } = useTranslation();
  // Which post is open in the side panel (null = closed)
  const [openPost, setOpenPost] = useState<number | null>(null);
  // Load dashboard numbers and service health (health refreshes every 15s)
  const stats = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  const health = useQuery({
    queryKey: ["healthz", "full"],
    queryFn: api.healthzFull,
    refetchInterval: 15_000,
  });

  return (
    <div className="mx-auto max-w-[1440px] px-4 pb-24 sm:px-8">
      {/* 1. Headline finding */}
      {stats.isError ? (
        <div className="pt-10">
          <QueryError what="corpus statistics" error={stats.error} onRetry={() => stats.refetch()} />
        </div>
      ) : (
        <Finding stats={stats.data} onCite={setOpenPost} />
      )}

      {/* 2. Graph of the evidence behind it */}
      {stats.data && stats.data.top_techniques[0] && (
        <EvidenceWeb techniqueId={stats.data.top_techniques[0].technique_id} onOpen={setOpenPost} />
      )}

      {/* 3. Status of DB / LLM / Tor / queue */}
      <SectionDivider label={t("home.sectionStatus")} trailing={t("home.statusMeta")} />
      {health.isError ? (
        <QueryError what="system status" error={health.error} onRetry={() => health.refetch()} />
      ) : (
        <StatusTable data={health.data} loading={health.isLoading} />
      )}

      {/* 4. Latest posts */}
      <SectionDivider
        label={t("home.sectionFeed")}
        trailing={stats.data ? t("home.postsIndexed", { count: stats.data.posts_total }) : ""}
      />
      <LatestPosts onOpen={setOpenPost} />

      {/* 5. Most common techniques */}
      <SectionDivider label={t("home.sectionMitre")} trailing={t("home.topTechniques")} />
      {stats.data && <TopTechniques data={stats.data.top_techniques} />}

      {/* Side panel for whichever post is clicked */}
      <DetailPanel id={openPost} onClose={() => setOpenPost(null)} />
    </div>
  );
}

/* Top finding: the claim + its source posts (left), how it was produced (right) */
function Finding({ stats, onCite }: { stats: Stats | undefined; onCite: (id: number) => void }) {
  const { t } = useTranslation();
  // The most common technique is the headline; load the posts behind it
  const top = stats?.top_techniques[0];
  const detail = useQuery({
    queryKey: ["technique", top?.technique_id],
    queryFn: () => api.technique(top!.technique_id),
    enabled: !!top,
  });
  // Up to 8 unique post ids to show as evidence buttons
  const cited = [...new Set((detail.data?.posts ?? []).map((p) => p.raw_post_id))].slice(0, 8);

  return (
    <section className="grid gap-x-12 gap-y-10 pt-10 pb-4 lg:grid-cols-12 lg:pt-16">
      {/* Left: headline, how it was found, evidence buttons */}
      <div className="lg:col-span-8">
        <h1 className="m-0 max-w-[18ch] text-4xl font-extrabold leading-[1.02] tracking-[-0.035em] sm:text-6xl">
          {top ? (
            <>
              <span className="text-accent">{top.name ?? top.technique_id}</span>{" "}
              {t("home.findingHeadline", {
                count: top.n,
                total: stats?.posts_total ?? 0,
              })}
            </>
          ) : (
            <span className="text-text-muted">{t("home.findingLoading")}</span>
          )}
        </h1>

        {top && (
          <div className="mt-8 max-w-[62ch]">
            <p className="m-0 text-[1rem] text-text-muted">
              <span className="font-mono text-text">{top.technique_id}</span> ·{" "}
              {t("home.findingProvenance", {
                verified: top.verified,
                semantic: top.semantic,
                unverified: top.unverified,
              })}
            </p>
            <div className="mt-4">
              <ProvenanceBar
                verified={top.verified}
                semantic={top.semantic}
                unverified={top.unverified}
                max={top.n}
              />
            </div>
            <div className="mt-6 flex flex-wrap items-center gap-2">
              <span className="mr-1 text-sm font-semibold">{t("home.evidence")}</span>
              {cited.map((id) => (
                <button
                  key={id}
                  onClick={() => onCite(id)}
                  className="border border-text px-2 py-0.5 font-mono text-sm hover:bg-text hover:text-surface-1"
                  title={t("home.openPost", { id })}
                >
                  #{id}
                </button>
              ))}
              <Link
                to="/techniques"
                className="ml-2 text-sm font-semibold text-accent underline hover:text-accent-strong"
              >
                {t("home.seeAllTechniques")}
              </Link>
            </div>
          </div>
        )}
      </div>

      {/* Right: how many items passed each pipeline step */}
      <aside className="lg:col-span-4" aria-label={t("home.custodyTitle")}>
        <h2 className="m-0 text-sm font-bold">{t("home.custodyTitle")}</h2>
        <ol className="m-0 mt-3 list-none p-0">
          {[
            [t("home.custodyScraped"), stats?.posts_total],
            [t("home.custodyIocs"), stats?.iocs_total],
            [t("home.custodyLlm"), stats?.posts_llm_analysed],
            [t("home.custodyMapped"), stats?.post_techniques_total],
            [t("home.custodyCorpus"), stats?.techniques_corpus],
          ].map(([label, n], i) => (
            <li
              key={i}
              className="flex items-baseline justify-between gap-4 border-t border-border-soft py-2.5 text-sm"
            >
              <span className="text-text-muted">{label}</span>
              <span className="tabular-nums font-semibold">{fmt(n as number | undefined)}</span>
            </li>
          ))}
        </ol>
        <p className="m-0 mt-3 text-xs leading-relaxed text-text-muted">{t("home.custodyNote")}</p>
      </aside>
    </section>
  );
}

/* System status table (only DB down is fatal) */
function StatusTable({ data, loading }: { data: HealthFull | undefined; loading: boolean }) {
  const { t } = useTranslation();
  // One row per service; only the DB being down is fatal
  const rows: { key: string; label: string; fatal: boolean; offlineNote: string }[] = [
    { key: "db", label: t("home.svcDb"), fatal: true, offlineNote: t("home.svcDbDown") },
    { key: "ollama", label: t("home.svcLlm"), fatal: false, offlineNote: t("home.svcLlmOffline") },
    { key: "tor_socks", label: t("home.svcTor"), fatal: false, offlineNote: t("home.svcTorOffline") },
  ];
  // Total posts still waiting in the pipeline
  const pipe = data?.checks.pipeline;
  const pending =
    Number(pipe?.pending_extraction ?? 0) + Number(pipe?.pending_llm ?? 0) + Number(pipe?.pending_mitre ?? 0);

  return (
    <table className="w-full border-collapse text-sm">
      <tbody>
        {rows.map((r) => {
          const c = data?.checks[r.key];
          const up = c?.status === "up";
          // Online / down (DB) / cached (LLM or Tor off) / checking
          const state = loading ? "checking" : up ? "online" : r.fatal ? "down" : "cached";
          return (
            <tr key={r.key} className="border-t border-border-soft align-baseline">
              <th scope="row" className="w-48 py-3 pr-4 text-left font-semibold">
                {r.label}
              </th>
              <td className="w-56 py-3 pr-4">
                <StatusWord state={state} />
              </td>
              <td className="py-3 text-text-muted">
                {state === "online" && c?.latency_ms !== undefined && (
                  <span className="tabular-nums">{c.latency_ms} ms</span>
                )}
                {(state === "cached" || state === "down") && r.offlineNote}
              </td>
            </tr>
          );
        })}
        {/* Last row: pipeline queue */}
        <tr className="border-t border-border-soft align-baseline">
          <th scope="row" className="py-3 pr-4 text-left font-semibold">
            {t("home.svcQueue")}
          </th>
          <td className="py-3 pr-4">
            <StatusWord state={loading ? "checking" : pending ? "busy" : "idle"} />
          </td>
          <td className="py-3 text-text-muted tabular-nums">
            {pipe &&
              t("home.queueDetail", {
                ex: pipe.pending_extraction ?? 0,
                llm: pipe.pending_llm ?? 0,
                mitre: pipe.pending_mitre ?? 0,
              })}
          </td>
        </tr>
      </tbody>
    </table>
  );
}

// Coloured status word with a small shape in front of it
function StatusWord({ state }: { state: "online" | "down" | "cached" | "checking" | "idle" | "busy" }) {
  const { t } = useTranslation();
  // Text colour per state
  const style = {
    online: "text-ok",
    idle: "text-text",
    busy: "text-warn",
    cached: "text-warn",
    down: "text-danger",
    checking: "text-text-muted",
  }[state];
  // Each state has its own shape, not just colour
  const mark = {
    online: "rounded-full bg-current",
    idle: "rounded-full border-2 border-current",
    busy: "rounded-full border-2 border-current [background:linear-gradient(90deg,currentColor_50%,transparent_50%)]",
    cached: "rounded-full border-2 border-current [background:linear-gradient(90deg,currentColor_50%,transparent_50%)]",
    down: "bg-current",
    checking: "rounded-full border-2 border-dashed border-current",
  }[state];
  return (
    <span className={`inline-flex items-center gap-2 font-semibold ${style}`}>
      <span aria-hidden className={`inline-block h-2.5 w-2.5 ${mark}`} />
      {t(`home.state.${state}`)}
    </span>
  );
}

// Table of the 8 newest posts; click a row to open it
function LatestPosts({ onOpen }: { onOpen: (id: number) => void }) {
  const { t } = useTranslation();
  const posts = useQuery({ queryKey: ["posts", { limit: 8 }], queryFn: () => api.posts({ limit: 8 }) });
  if (posts.isError)
    return <QueryError what="latest posts" error={posts.error} onRetry={() => posts.refetch()} />;
  if (posts.isLoading) return <p className="text-sm text-text-muted">{t("home.loadingFeed")}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <thead>
          <tr className="text-left text-xs text-text-muted">
            <th className="py-2 pr-4 font-semibold">{t("home.colPost")}</th>
            <th className="py-2 pr-4 font-semibold">{t("home.colTitle")}</th>
            <th className="py-2 pr-4 font-semibold">{t("home.colCategory")}</th>
            <th className="py-2 pr-4 font-semibold">{t("home.colIntent")}</th>
            <th className="py-2 text-right font-semibold">{t("home.colPosted")}</th>
          </tr>
        </thead>
        <tbody>
          {posts.data?.items.map((p) => (
            <tr
              key={p.id}
              className="cursor-pointer border-t border-border-soft align-baseline hover:bg-surface-1"
              onClick={() => onOpen(p.id)}
            >
              <td className="py-2.5 pr-4 font-mono">
                <button
                  className="font-mono text-text hover:text-accent"
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpen(p.id);
                  }}
                >
                  #{p.id}
                </button>
              </td>
              <td className="py-2.5 pr-4 font-semibold">{p.thread_title}</td>
              <td className="py-2.5 pr-4 text-text-muted">{p.category}</td>
              <td className="py-2.5 pr-4 font-semibold" style={{ color: intentColor(p.intent) }}>
                {p.intent ?? "—"}
              </td>
              <td className="py-2.5 text-right text-text-muted tabular-nums">
                <time dateTime={new Date(p.source_created_at * 1000).toISOString()}>
                  {new Date(p.source_created_at * 1000).toISOString().slice(0, 10)}
                </time>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Link to="/posts" className="mt-3 inline-block text-sm font-semibold text-accent underline">
        {t("home.openFeed")}
      </Link>
    </div>
  );
}

// Top 10 techniques with a bar showing how they were found
function TopTechniques({ data }: { data: Stats["top_techniques"] }) {
  const { t } = useTranslation();
  if (!data.length) return <p className="text-sm text-text-muted">{t("common.noData")}</p>;
  // Longest bar = most mentioned technique
  const max = Math.max(...data.map((d) => d.n));
  return (
    <>
      <div className="mb-6 bg-surface-1 p-4">
        <ProvenanceLegend />
      </div>
      <ol className="m-0 list-none p-0">
        {data.slice(0, 10).map((d) => (
          <li
            key={d.technique_id}
            className="grid grid-cols-[5.5rem_1fr_3rem] items-center gap-x-4 gap-y-1.5 border-t border-border-soft py-2.5 text-sm sm:grid-cols-[5.5rem_16rem_1fr_3rem]"
          >
            <span className="font-mono">{d.technique_id}</span>
            {d.name ? (
              <span className="truncate font-semibold">{d.name}</span>
            ) : (
              <span className="truncate text-warn">{t("home.notInCorpus")}</span>
            )}
            <span className="col-span-3 sm:col-span-1">
              <ProvenanceBar verified={d.verified} semantic={d.semantic} unverified={d.unverified} max={max} />
            </span>
            <span className="row-start-1 col-start-3 text-right tabular-nums sm:row-auto sm:col-auto">{d.n}</span>
          </li>
        ))}
      </ol>
    </>
  );
}

/* Mini graph of the top finding's posts, IOCs and techniques */
// Graph of the top technique's posts (up to 18), their IOCs and techniques
function EvidenceWeb({ techniqueId, onOpen }: { techniqueId: string; onOpen: (id: number) => void }) {
  const { t } = useTranslation();
  const detail = useQuery({ queryKey: ["technique", techniqueId], queryFn: () => api.technique(techniqueId) });
  const ids = [...new Set((detail.data?.posts ?? []).map((p) => p.raw_post_id))].slice(0, 18);
  // Load every post's details in parallel
  const postsQ = useQueries({ queries: ids.map((id) => ({ queryKey: ["post", id], queryFn: () => api.post(id) })) });
  const posts = postsQ.map((q) => q.data).filter((p): p is PostDetail => !!p);
  // Wait until all of them have loaded
  const ready = ids.length > 0 && postsQ.every((q) => !q.isLoading);
  return (
    <>
      <SectionDivider label={t("home.webTitle")} trailing={t("home.webMeta", { n: ids.length })} />
      <p className="m-0 mb-4 max-w-[70ch] text-sm text-text-muted">{t("home.webIntro")}</p>
      {detail.isError ? (
        <QueryError what="the evidence web" error={detail.error} onRetry={() => detail.refetch()} />
      ) : !ready ? (
        <div className="grid h-[420px] place-items-center border border-dashed border-border-soft text-sm text-text-muted">
          {t("home.webLoading")}
        </div>
      ) : (
        <Suspense fallback={<div className="route-loading" />}>
          <EvidenceGraph posts={posts} onSelectPost={onOpen} height={520} focusTechnique={techniqueId} />
        </Suspense>
      )}
    </>
  );
}
