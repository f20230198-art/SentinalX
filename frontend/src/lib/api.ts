/**
 * Tiny typed wrapper around the SentinelX API.
 *
 * In dev, vite.config.ts proxies /api/* to http://127.0.0.1:8765 so we keep
 * everything same-origin (no CORS preflights). In prod, we'd serve the built
 * SPA from the same FastAPI process and drop the proxy.
 */

const BASE = "/api";

async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const qs = params
    ? "?" +
      new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== null && v !== "")
          .map(([k, v]) => [k, String(v)]),
      ).toString()
    : "";
  const r = await fetch(`${BASE}${path}${qs}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on GET ${path}`);
  return r.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on POST ${path}`);
  return r.json() as Promise<T>;
}

// --- Types mirror the FastAPI shapes. Kept loose on purpose; we tighten
//     when we actually consume each field. -------------------------------

export interface Stats {
  posts_total: number;
  posts_extracted: number;
  posts_llm_analysed: number;
  posts_mitre_matched: number;
  iocs_total: number;
  entities_total: number;
  techniques_corpus: number;
  post_techniques_total: number;
  by_category: { category: string; n: number }[];
  by_intent: { intent: string; n: number }[];
  by_ioc_type: { ioc_type: string; n: number }[];
  by_technique_source: { source: string; n: number }[];
  top_techniques: { technique_id: string; name: string | null; n: number }[];
}

export interface PostListItem {
  id: number;
  thread_title: string;
  category: string;
  author: string;
  body_preview: string;
  source_created_at: number;
  intent: string | null;
  summary: string | null;
}

export interface PostList {
  total: number;
  limit: number;
  offset: number;
  items: PostListItem[];
}

export interface HealthFull {
  status: "ok" | "degraded";
  checks: Record<
    string,
    {
      status: "up" | "down";
      latency_ms?: number;
      error?: string;
      [k: string]: unknown;
    }
  >;
}

export interface Lens {
  name: string;
  label: string;
  description: string;
}

export interface Investigation {
  id: number;
  name: string;
  description: string | null;
  lens: string | null;
  summary: string | null;
  summary_model: string | null;
  summary_post_ids: number[];
  filters: Record<string, unknown>;
  created_at: number;
  updated_at: number;
  last_run_at: number | null;
  matched_total?: number;
  matched_posts?: PostListItem[];
}

export const api = {
  healthz: () => get<{ status: string }>("/healthz"),
  healthzFull: () => get<HealthFull>("/healthz/full"),
  stats: () => get<Stats>("/stats"),
  posts: (params?: Record<string, unknown>) => get<PostList>("/posts", params),
  post: (id: number) => get<unknown>(`/posts/${id}`),
  lenses: () => get<{ items: Lens[] }>("/lenses"),
  investigations: () => get<{ items: Investigation[] }>("/investigations"),
  investigation: (id: number) => get<Investigation>(`/investigations/${id}`),
  createInvestigation: (body: {
    name: string;
    description?: string;
    filters: Record<string, unknown>;
    lens?: string;
  }) => post<Investigation>("/investigations", body),
  rerun: (id: number) => post<Investigation>(`/investigations/${id}/rerun`, {}),
};
