import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import { api, type PostDetail } from "../lib/api";
import { SectionDivider } from "../components/Shell";
import { DetailPanel } from "../components/DetailPanel";
import { QueryError } from "../components/Evidence";
import { EvidenceGraph } from "../components/EvidenceGraph";

/* Investigation graph (/investigations/:id/graph): posts linked to their IOCs and techniques */

// Max posts drawn, so the graph stays readable
const MAX_POSTS = 25;

// Investigation id from the URL, and the post open in the side panel
export function CaseGraph() {
  const { id } = useParams<{ id: string }>();
  const investigationId = id ? Number(id) : NaN;
  const [selectedPost, setSelectedPost] = useState<number | null>(null);

  // Load the investigation and its matching posts
  const inv = useQuery({
    queryKey: ["investigation", investigationId],
    queryFn: () => api.investigation(investigationId),
    enabled: Number.isFinite(investigationId),
  });

  // Ids of the first 25 matching posts
  const matchedIds = useMemo(
    () =>
      (inv.data?.matched_posts ?? [])
        .slice(0, MAX_POSTS)
        .map((p) => p.id),
    [inv.data],
  );

  // Load each post's details in parallel
  const postsQ = useQueries({
    queries: matchedIds.map((pid) => ({
      queryKey: ["post", pid],
      queryFn: () => api.post(pid),
    })),
  });

  // Only the posts that have finished loading
  const posts = useMemo(
    () =>
      postsQ
        .map((q) => q.data)
        .filter((p): p is PostDetail => Boolean(p)),
    [postsQ],
  );

  const loadingPosts = postsQ.some((q) => q.isLoading);

  // Bad id in the URL
  if (!Number.isFinite(investigationId)) {
    return (
      <div className="mx-auto max-w-[1440px] px-4 pt-12 sm:px-8">
        <p className="text-sm text-text-muted">No investigation in the address.</p>
      </div>
    );
  }

  // More matches than we draw?
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
          <span className="mr-2 font-mono text-[1rem] font-normal text-text-muted">#{inv.data?.id}</span>
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

      {/* Loading, empty, or the graph */}
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
