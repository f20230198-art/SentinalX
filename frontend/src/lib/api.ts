/**
 * Tiny typed wrapper around the SentinelX API.
 *
 * In dev, vite.config.ts proxies /api/* to http://127.0.0.1:8765 so we keep
 * everything same-origin (no CORS preflights). In prod (Vercel), set
 * VITE_API_BASE to the Render URL, e.g. https://sentinelx-api.onrender.com.
 */

export const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ??
  "/api";
const BASE = API_BASE;

// Optional: only needed when the backend sets SENTINELX_API_KEY. Sent on
// writes only — reads are open.
const API_KEY = import.meta.env.VITE_API_KEY as string | undefined;
const writeHeaders = (): Record<string, string> =>
  API_KEY ? { "x-api-key": API_KEY } : {};

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
    headers: { "content-type": "application/json", ...writeHeaders() },
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
  top_techniques: {
    technique_id: string;
    name: string | null;
    n: number;
    verified: number;
    semantic: number;
    unverified: number;
  }[];
  /** Language mix of the ingested corpus. */
  by_language: { lang: string; n: number }[];
  posts_translated: number;
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
  /** Detected ISO language of the post ('en','ru',…) or 'unknown'.
   *  null for posts ingested before multilingual support landed. */
  lang: string | null;
  lang_confidence: number | null;
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

export interface TimelinePost {
  id: number;
  thread_title: string;
  category: string;
  author: string;
  body_preview: string;
  source_created_at: number;
  intent: string | null;
  summary: string | null;
  /** Detected language of the post, or null for posts ingested before
   *  multilingual support landed. */
  lang: string | null;
  lang_confidence: number | null;
  techniques: { technique_id: string; source: string; name: string | null }[];
  iocs: { ioc_type: string; value: string }[];
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
  mitigations?: InvestigationMitigation[];
}

/** A MITRE mitigation as resolved for a single post — defensive recommendation
 *  driven purely by the post's technique mappings (no LLM). `addresses` lists
 *  which of the post's T-codes this mitigation counters. */
export interface PostMitigation {
  mitigation_id: string;
  name: string;
  description: string;
  url: string | null;
  addresses: string[];
  coverage: number;
}

/** A MITRE mitigation aggregated across an investigation's matched posts,
 *  ranked by how many of those posts it would help defend. */
export interface InvestigationMitigation {
  mitigation_id: string;
  name: string;
  description: string;
  url: string | null;
  posts_covered: number;
  post_share: number;
  techniques: string[];
}

export interface PostDetail {
  post: {
    id: number;
    thread_title: string;
    category: string;
    author: string;
    body: string;
    source_created_at: number;
    /** Language metadata. lang is the detected ISO code; body_en is the
     *  English translation when the post is non-English (null otherwise, and
     *  null for posts ingested before multilingual support landed). */
    lang: string | null;
    lang_confidence: number | null;
    body_en: string | null;
  };
  analysis: {
    intent: string | null;
    summary: string | null;
    targets: unknown;
    techniques: unknown;
  } | null;
  iocs: { ioc_type: string; value: string }[];
  entities: { label: string; text: string }[];
  techniques: {
    technique_id: string;
    source: string;
    score: number | null;
    name: string | null;
    tactics: string[] | null;
  }[];
  mitigations: PostMitigation[];
}

export interface TechniqueListItem {
  technique_id: string;
  name: string | null;
  tactics: string[] | null;
  url: string | null;
  is_subtechnique: number;
  parent_id: string | null;
  post_count: number;
}

export interface TechniqueList {
  total: number;
  limit: number;
  offset: number;
  items: TechniqueListItem[];
}

export interface TechniqueDetail {
  technique_id: string;
  name: string | null;
  description: string | null;
  tactics: string[] | null;
  url: string | null;
  is_subtechnique: number;
  parent_id: string | null;
  posts: {
    raw_post_id: number;
    source: string;
    score: number | null;
    thread_title: string;
    category: string;
  }[];
}

export interface IocAggItem {
  ioc_type: string;
  value: string;
  occurrences: number;
  post_ids: number[];
}

export interface IocList {
  total: number;
  limit: number;
  offset: number;
  items: IocAggItem[];
}

/** A pipeline job: one end-to-end run triggered by pasting an .onion URL.
 *  scrape (HTML) -> extract -> LLM -> MITRE -> mitigations. */
export interface ScrapeJob {
  id: number;
  onion_url: string;
  source: string;
  status: "queued" | "running" | "done" | "error";
  stage: string;
  posts_scraped: number;
  posts_extracted: number;
  posts_llm: number;
  techniques_mapped: number;
  llm_skipped: number;
  message: string | null;
  error: string | null;
  created_at: number;
  updated_at: number;
  finished_at: number | null;
}

export interface DiscoverResult {
  engine: string;
  title: string;
  url: string;
  snippet: string;
  onion: string | null;
  post_id: number | null;
  score: number;
}

export interface DiscoverResponse {
  query: string;
  refined: string | null;
  refine_error: string | null;
  results: DiscoverResult[];
  errors: Record<string, string>;
}

export interface Watchlist {
  id: number;
  name: string;
  terms: string[];
  created_at: number;
  hits: number;
  unseen: number;
}

export interface Alert {
  id: number;
  watchlist_id: number;
  watchlist: string;
  raw_post_id: number;
  matched_terms: string[];
  seen: boolean;
  thread_title: string;
  category: string;
  source: string;
  source_created_at: number;
  intent: string | null;
}

export const api = {
  healthz: () => get<{ status: string }>("/healthz"),
  healthzFull: () => get<HealthFull>("/healthz/full"),
  stats: () => get<Stats>("/stats"),
  posts: (params?: Record<string, unknown>) => get<PostList>("/posts", params),
  post: (id: number) => get<PostDetail>(`/posts/${id}`),
  techniques: (params?: Record<string, unknown>) =>
    get<TechniqueList>("/techniques", params),
  technique: (id: string) => get<TechniqueDetail>(`/techniques/${id}`),
  iocs: (params?: Record<string, unknown>) => get<IocList>("/iocs", params),
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
  scrapeJobs: () => get<{ items: ScrapeJob[] }>("/scrape-jobs"),
  scrapeJob: (id: number) => get<ScrapeJob>(`/scrape-jobs/${id}`),
  watchlists: () => get<{ items: Watchlist[] }>("/watchlists"),
  createWatchlist: (body: { name: string; terms: string[] }) => post<Watchlist>("/watchlists", body),
  deleteWatchlist: async (id: number): Promise<void> => {
    const r = await fetch(`${BASE}/watchlists/${id}`, { method: "DELETE", headers: writeHeaders() });
    if (!r.ok && r.status !== 204) throw new Error(`${r.status} ${r.statusText}`);
  },
  alerts: (watchlistId?: number) =>
    get<{ items: Alert[]; unseen: number }>("/alerts", { watchlist_id: watchlistId }),
  markAlertsSeen: (ids?: number[]) => post<{ updated: number }>("/alerts/seen", { ids }),
  discoverEngines: () =>
    get<{ items: { name: string; label: string; needs_tor: boolean }[] }>("/discover/engines"),
  discover: (body: { query: string; engines: string[]; refine: boolean }) =>
    post<DiscoverResponse>("/discover/search", body),
  sendPages: (urls: string[], skip_llm = false) =>
    post<ScrapeJob>("/scrape-jobs", { urls, skip_llm }),
  startScrapeJob: (body: {
    onion_url: string;
    source?: string;
    skip_llm?: boolean;
  }) => post<ScrapeJob>("/scrape-jobs", body),
  exportInvestigationUrl: (id: number) =>
    `${BASE}/investigations/${id}/export`,
  deleteInvestigation: async (id: number): Promise<void> => {
    const r = await fetch(`${BASE}/investigations/${id}`, {
      method: "DELETE",
      headers: writeHeaders(),
    });
    if (!r.ok && r.status !== 204)
      throw new Error(`${r.status} ${r.statusText}`);
  },
};
