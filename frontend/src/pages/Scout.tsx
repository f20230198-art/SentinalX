import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { api, type ScrapeJob } from "../lib/api";
import { SectionDivider } from "../components/Shell";

/* ----------------------------------------------------------------------- *
 * Scout — point SentinelX at an arbitrary darknet forum.
 *
 * Paste a .onion URL, hit RUN PIPELINE, and the backend scrapes it (HTML),
 * extracts IOCs, enriches with the local LLM, and maps it to MITRE ATT&CK —
 * all on demand, for a forum it has never seen before. The job runs in a
 * backend thread; this page polls its status and renders live progress.
 * ----------------------------------------------------------------------- */

// The pipeline stages, in order — drives the progress checklist.
const STAGES: { key: string; label: string }[] = [
  { key: "scraping", label: "Scrape forum (HTML over Tor)" },
  { key: "extracting", label: "Extract IOCs + entities" },
  { key: "llm", label: "LLM enrichment" },
  { key: "mitre", label: "Map to MITRE ATT&CK" },
  { key: "done", label: "Done" },
];

// Where a given stage sits in the ordered list (-1 if unknown / queued).
function stageIndex(stage: string): number {
  // checking-ollama is a sub-step of the llm stage.
  const s = stage === "checking-ollama" ? "llm" : stage;
  return STAGES.findIndex((x) => x.key === s);
}

export function Scout() {
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const [skipLlm, setSkipLlm] = useState(false);
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const jobs = useQuery({ queryKey: ["scrape-jobs"], queryFn: api.scrapeJobs });

  // Poll the active job every 2s while it is queued/running.
  const active = useQuery({
    queryKey: ["scrape-job", activeJobId],
    queryFn: () => api.scrapeJob(activeJobId!),
    enabled: activeJobId !== null,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "done" || s === "error" ? false : 2000;
    },
  });

  const start = useMutation({
    mutationFn: (body: { onion_url: string; skip_llm: boolean }) =>
      api.startScrapeJob(body),
    onSuccess: (job) => {
      setActiveJobId(job.id);
      setUrl("");
      qc.invalidateQueries({ queryKey: ["scrape-jobs"] });
    },
    onError: (e: Error) => setFormError(e.message),
  });

  function submit() {
    setFormError(null);
    const v = url.trim();
    if (!v) {
      setFormError("paste a .onion URL first");
      return;
    }
    if (!v.replace(/^https?:\/\//, "").split("/")[0].endsWith(".onion")) {
      setFormError("that doesn't look like a .onion address");
      return;
    }
    start.mutate({ onion_url: v, skip_llm: skipLlm });
  }

  // When a polled job finishes, refresh the job list once.
  if (
    active.data &&
    (active.data.status === "done" || active.data.status === "error") &&
    jobs.data &&
    !jobs.data.items.some(
      (j) => j.id === active.data!.id && j.status === active.data!.status,
    )
  ) {
    qc.invalidateQueries({ queryKey: ["scrape-jobs"] });
  }

  return (
    <div className="max-w-[1100px] mx-auto px-8">
      <SectionDivider
        index="05"
        label="Scout"
        trailing="point SentinelX at any darknet forum"
      />

      {/* --- the URL bar --- */}
      <div className="border border-border-soft bg-surface-1/40 p-5">
        <div className="font-mono text-[10px] tracking-[0.2em] text-text-muted mb-2">
          TARGET FORUM
        </div>
        <div className="flex gap-2">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder="paste a .onion address — e.g. abcd…xyz.onion"
            className="flex-1 bg-base/60 border border-border-soft px-3 py-2 font-mono text-xs focus:border-accent outline-none"
          />
          <button
            onClick={submit}
            disabled={start.isPending}
            className="font-mono text-[11px] tracking-[0.2em] border border-accent text-accent px-4 py-2 hover:bg-accent/10 disabled:opacity-30 transition-colors whitespace-nowrap"
          >
            [ {start.isPending ? "STARTING…" : "RUN PIPELINE"} ]
          </button>
        </div>
        {formError && (
          <div className="font-mono text-[10px] text-danger mt-2">
            {formError}
          </div>
        )}

        <label className="flex items-center gap-2 mt-3 cursor-pointer select-none w-fit">
          <input
            type="checkbox"
            checked={skipLlm}
            onChange={(e) => setSkipLlm(e.target.checked)}
            className="accent-accent"
          />
          <span className="font-mono text-[10px] text-text-muted">
            FAST MODE — skip LLM enrichment{" "}
            <span className="opacity-60">
              (scrape + IOCs + MITRE only; use on battery)
            </span>
          </span>
        </label>

        <p className="font-mono text-[10px] text-text-muted mt-3 leading-relaxed">
          SentinelX will scrape the forum's HTML over Tor, extract IOCs,{" "}
          {skipLlm ? (
            <span className="text-warn">skip LLM enrichment (fast mode),</span>
          ) : (
            "enrich each post with the local LLM,"
          )}{" "}
          and map it to MITRE ATT&amp;CK. Works on any SilkVault-structured
          forum — bring up your own and paste its address.
        </p>
      </div>

      {/* --- live progress for the active job --- */}
      <AnimatePresence>
        {active.data && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-6"
          >
            <JobProgress job={active.data} />
          </motion.div>
        )}
      </AnimatePresence>

      {/* --- recent jobs --- */}
      <section className="mt-10">
        <div className="font-mono text-[10px] tracking-[0.2em] text-text-muted mb-3">
          RECENT RUNS
        </div>
        {jobs.isLoading && (
          <div className="font-mono text-xs text-text-muted">loading…</div>
        )}
        {jobs.data && jobs.data.items.length === 0 && (
          <div className="font-mono text-xs text-text-muted opacity-60 italic">
            no pipeline runs yet — paste a forum URL above to start one.
          </div>
        )}
        <ul className="space-y-1.5">
          {jobs.data?.items.map((j) => (
            <li key={j.id}>
              <button
                onClick={() => setActiveJobId(j.id)}
                className={`w-full text-left border px-3 py-2 transition-colors ${
                  activeJobId === j.id
                    ? "border-accent bg-accent/5"
                    : "border-border-soft hover:border-text-muted"
                }`}
              >
                <div className="flex items-baseline gap-2 font-mono text-xs">
                  <span className="text-text-muted">#{j.id}</span>
                  <StatusPill status={j.status} />
                  <span className="text-text truncate">{j.source}</span>
                  <span className="ml-auto text-text-muted text-[10px]">
                    {j.posts_scraped} scraped · {j.techniques_mapped} mapped
                  </span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <div className="h-24" />
    </div>
  );
}

function JobProgress({ job }: { job: ScrapeJob }) {
  const current = stageIndex(job.stage);
  const failed = job.status === "error";

  return (
    <div className="border border-border-soft bg-surface-1/40 p-5">
      <div className="flex items-baseline gap-3 mb-4">
        <span className="font-mono text-[10px] text-text-muted">
          JOB #{job.id}
        </span>
        <StatusPill status={job.status} />
        <span className="font-mono text-xs text-text truncate">
          {job.onion_url}
        </span>
      </div>

      <ol className="space-y-2">
        {STAGES.map((s, i) => {
          const state =
            failed && i === current
              ? "failed"
              : i < current || job.status === "done"
                ? "done"
                : i === current
                  ? "active"
                  : "pending";
          return (
            <li
              key={s.key}
              className="flex items-center gap-3 font-mono text-xs"
            >
              <StageGlyph state={state} />
              <span
                className={
                  state === "pending"
                    ? "text-text-muted opacity-50"
                    : state === "failed"
                      ? "text-danger"
                      : state === "active"
                        ? "text-accent"
                        : "text-text"
                }
              >
                {s.label}
              </span>
              {s.key === "llm" && state === "active" && (
                <span className="text-text-muted text-[10px]">
                  {job.posts_llm > 0 ? `${job.posts_llm} posts` : "starting…"}
                </span>
              )}
              {s.key === "llm" && job.llm_skipped === 1 && (
                <span className="text-warn text-[10px]">
                  skipped — Ollama unreachable
                </span>
              )}
            </li>
          );
        })}
      </ol>

      {job.message && (
        <div className="mt-4 font-mono text-[11px] text-text-muted border-t border-border-soft pt-3">
          {job.message}
        </div>
      )}
      {job.error && (
        <div className="mt-2 font-mono text-[11px] text-danger">
          error: {job.error}
        </div>
      )}

      {job.status === "done" && job.posts_scraped > 0 && (
        <div className="mt-4 grid grid-cols-3 gap-2 font-mono text-xs">
          <Stat n={job.posts_scraped} label="scraped" />
          <Stat n={job.posts_llm} label="LLM-analysed" />
          <Stat n={job.techniques_mapped} label="ATT&CK mappings" />
        </div>
      )}
    </div>
  );
}

function StageGlyph({ state }: { state: string }) {
  if (state === "done") return <span className="text-accent w-4">✓</span>;
  if (state === "failed") return <span className="text-danger w-4">✗</span>;
  if (state === "active")
    return (
      <motion.span
        className="text-accent w-4"
        animate={{ opacity: [1, 0.3, 1] }}
        transition={{ duration: 1.2, repeat: Infinity }}
      >
        ▸
      </motion.span>
    );
  return <span className="text-text-muted w-4 opacity-40">·</span>;
}

function StatusPill({ status }: { status: ScrapeJob["status"] }) {
  const color =
    status === "done"
      ? "text-accent border-accent/40"
      : status === "error"
        ? "text-danger border-danger/40"
        : "text-warn border-warn/40";
  return (
    <span
      className={`font-mono text-[9px] tracking-[0.15em] uppercase border px-1.5 py-0.5 ${color}`}
    >
      {status}
    </span>
  );
}

function Stat({ n, label }: { n: number; label: string }) {
  return (
    <div className="border border-border-soft bg-base/40 px-3 py-2">
      <div className="text-accent text-base">{n}</div>
      <div className="text-text-muted text-[10px]">{label}</div>
    </div>
  );
}
