import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { api, type HealthFull } from "../lib/api";

/* ----------------------------------------------------------------------- *
 * Persistent header indicator. Polls /healthz/full every 12s and surfaces:
 *   - overall status pill (OK / DEGRADED)
 *   - pipeline pending counts (extraction / llm / mitre)
 *   - hover popover with per-check status + latency
 * Sells the "continuous monitoring" identity without taking real estate.
 * ----------------------------------------------------------------------- */

const POLL_MS = 12_000;

type CheckMap = HealthFull["checks"];

function pendingTotal(checks: CheckMap | undefined): number {
  const p = checks?.pipeline;
  if (!p) return 0;
  return (
    Number(p.pending_extraction ?? 0) +
    Number(p.pending_llm ?? 0) +
    Number(p.pending_mitre ?? 0)
  );
}

export function WatchIndicator() {
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["healthz", "full"],
    queryFn: api.healthzFull,
    refetchInterval: POLL_MS,
    refetchOnWindowFocus: true,
  });

  const checks = q.data?.checks;
  const overall = q.data?.status ?? (q.isLoading ? "loading" : "down");
  const pending = pendingTotal(checks);
  const isOk = overall === "ok";

  const dot = q.isLoading
    ? "bg-text-muted animate-pulse"
    : isOk
    ? "bg-accent shadow-[0_0_6px_var(--color-accent)]"
    : "bg-danger shadow-[0_0_6px_rgba(229,72,77,0.7)]";

  return (
    <div
      className="relative"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        className="flex items-center gap-2 font-mono text-[10px] tracking-[0.2em] border border-border-soft px-3 py-1.5 hover:border-text-muted transition-colors"
        aria-expanded={open}
      >
        <span className={`inline-block w-1.5 h-1.5 rounded-full ${dot}`} />
        <span className={isOk ? "text-text" : "text-danger"}>
          {q.isLoading
            ? "BOOTING"
            : isOk
            ? pending > 0
              ? "PROCESSING"
              : "IDLE"
            : "DEGRADED"}
        </span>
        {pending > 0 && (
          <span className="text-text-muted tabular-nums">· {pending} q</span>
        )}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.15 }}
            className="absolute right-0 top-full mt-2 w-72 z-50 border border-border-soft bg-base/95 backdrop-blur-md p-3 font-mono text-[11px]"
          >
            <Row label="db" check={checks?.db} />
            <Row label="ollama" check={checks?.ollama} />
            <Row label="tor" check={checks?.tor_socks} />
            <div className="my-2 border-t border-border-soft/60" />
            <PipelineRow
              label="extraction"
              n={Number(checks?.pipeline?.pending_extraction ?? 0)}
            />
            <PipelineRow
              label="llm"
              n={Number(checks?.pipeline?.pending_llm ?? 0)}
            />
            <PipelineRow
              label="mitre"
              n={Number(checks?.pipeline?.pending_mitre ?? 0)}
            />
            <div className="mt-2 text-[9px] text-text-muted opacity-70">
              polled every {POLL_MS / 1000}s
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function Row({
  label,
  check,
}: {
  label: string;
  check: CheckMap[string] | undefined;
}) {
  const up = check?.status === "up";
  return (
    <div className="flex items-baseline gap-2 py-0.5">
      <span
        className={`inline-block w-1.5 h-1.5 rounded-full ${
          up ? "bg-accent" : "bg-danger"
        }`}
      />
      <span className="text-text-muted uppercase tracking-[0.15em] text-[9px]">
        {label}
      </span>
      <span className={`ml-auto ${up ? "text-text" : "text-danger"}`}>
        {check?.status?.toUpperCase() ?? "—"}
      </span>
      {check?.latency_ms !== undefined && (
        <span className="text-text-muted tabular-nums text-[9px]">
          {check.latency_ms}ms
        </span>
      )}
    </div>
  );
}

function PipelineRow({ label, n }: { label: string; n: number }) {
  return (
    <div className="flex items-baseline gap-2 py-0.5">
      <span
        className={`inline-block w-1.5 h-1.5 rounded-full ${
          n === 0 ? "bg-accent/50" : "bg-warn animate-pulse"
        }`}
      />
      <span className="text-text-muted uppercase tracking-[0.15em] text-[9px]">
        {label}
      </span>
      <span className="ml-auto tabular-nums">
        {n === 0 ? (
          <span className="text-text-muted">idle</span>
        ) : (
          <span className="text-warn">{n} pending</span>
        )}
      </span>
    </div>
  );
}
