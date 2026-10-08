import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import { api, type PostDetail } from "../lib/api";
import { SectionDivider } from "../components/Shell";
import { DetailPanel } from "../components/DetailPanel";
import { QueryError } from "../components/Evidence";
import { EvidenceGraph } from "../components/EvidenceGraph";

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

export function CaseGraph() {
  const { id } = useParams<{ id: string }>();
  const investigationId = id ? Number(id) : NaN;
  const [selectedPost, setSelectedPost] = useState<number | null>(null);

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
      <div className="mx-auto max-w-[1440px] px-4 pt-12 sm:px-8">
        <p className="text-sm text-text-muted">No investigation in the address.</p>
      </div>
    );
  }

  const totalMatched = inv.data?.matched_total ?? 0;
  const truncated = totalMatched > MAX_POSTS;

  return (
    <div className="mx-auto max-w-[1840px] px-4 pb-24 sm:px-8">
      <SectionDivider
        index="06"
        label="Case graph"
        trailing={
          inv.isLoading
            ? "Loading…"
            : `${matchedIds.length}${truncated ? "+" : ""} posts · ${posts.length}/${matchedIds.length} loaded`
        }
      />

      <div className="mb-6 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h1 className="m-0 text-2xl font-extrabold tracking-[-0.02em]">
          <span className="mr-2 font-mono text-base font-normal text-text-muted">#{inv.data?.id}</span>
          {inv.data?.name}
        </h1>
        <span className="text-sm text-text-muted">
          Posts linked by the indicators and techniques they share. Clusters are shared evidence.
        </span>
        <Link to="/investigations" className="ml-auto text-sm font-semibold text-accent underline">
          Back to investigations
        </Link>
      </div>
      {inv.isError && <QueryError what="this investigation" error={inv.error} onRetry={() => inv.refetch()} />}

      {inv.isLoading ? (
        <p className="text-sm text-text-muted">Loading the investigation…</p>
      ) : matchedIds.length === 0 ? (
        <p className="border border-dashed border-border-soft p-8 text-sm text-text-muted">
          No posts match this investigation's filter yet.
        </p>
      ) : loadingPosts ? (
        <p className="border border-dashed border-border-soft p-8 text-sm text-text-muted">
          Fetching {matchedIds.length} posts…
        </p>
      ) : (
        <EvidenceGraph posts={posts} onSelectPost={setSelectedPost} height={720} />
      )}


      <DetailPanel
        id={selectedPost}
        onClose={() => setSelectedPost(null)}
      />

      <div className="h-32" />
    </div>
  );
}
