import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type HealthFull } from "../lib/api";

/* ----------------------------------------------------------------------- *
 * Header status pill. Polls /healthz/full every 12s.
 *
 * Three honest states, not two:
 *   Live         — database, LLM and Tor all up
 *   Cached mode  — database up, LLM and/or Tor offline: everything stored is
 *                  still browsable, only new enrichment/scraping pauses
 *   Down         — database unreachable (the only fatal case)
 * ----------------------------------------------------------------------- */

const POLL_MS = 12_000;

type Mode = "live" | "cached" | "down" | "checking";

function modeOf(h: HealthFull | undefined, loading: boolean, failed: boolean): Mode {
  if (loading) return "checking";
  if (failed || h?.checks.db?.status !== "up") return "down";
  const optional = [h.checks.ollama, h.checks.tor_socks];
  return optional.every((c) => c?.status === "up") ? "live" : "cached";
}

export function WatchIndicator() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["healthz", "full"],
    queryFn: api.healthzFull,
    refetchInterval: POLL_MS,
    refetchOnWindowFocus: true,
  });
  const mode = modeOf(q.data, q.isLoading, q.isError);
  const p = q.data?.checks.pipeline;
  const pending =
    Number(p?.pending_extraction ?? 0) + Number(p?.pending_llm ?? 0) + Number(p?.pending_mitre ?? 0);

  const tone = {
    live: "text-ok border-ok/40",
    cached: "text-warn border-warn/40",
    down: "text-danger border-danger/50",
    checking: "text-text-muted border-border-soft",
  }[mode];

  return (
    <div className="relative" onMouseLeave={() => setOpen(false)}>
      <button
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={() => setOpen(true)}
        aria-expanded={open}
        aria-controls="watch-popover"
        className={`flex items-center gap-2 border px-2.5 py-1 text-xs font-semibold ${tone}`}
      >
        <span
          aria-hidden
          className={`inline-block h-2 w-2 rounded-full ${
            mode === "live" ? "bg-current" : mode === "down" ? "rounded-none bg-current" : "border-2 border-current"
          }`}
        />
        {t(`watch.${mode}`)}
        {pending > 0 && <span className="font-normal tabular-nums text-text-muted">· {pending} queued</span>}
      </button>

      {open && (
        <div
          id="watch-popover"
          role="dialog"
          aria-label={t("watch.detail")}
          className="absolute right-0 top-full z-50 mt-2 w-80 border border-text bg-surface-1 p-4 text-sm shadow-[0_8px_24px_rgba(0,0,0,0.5)]"
        >
          <p className="m-0 mb-3 text-xs leading-relaxed text-text-muted">{t(`watch.explain.${mode}`)}</p>
          <dl className="m-0">
            {(
              [
                ["db", t("home.svcDb")],
                ["ollama", t("home.svcLlm")],
                ["tor_socks", t("home.svcTor")],
              ] as const
            ).map(([k, label]) => {
              const c = q.data?.checks[k];
              const up = c?.status === "up";
              return (
                <div key={k} className="flex items-baseline justify-between border-t border-border-soft py-1.5">
                  <dt>{label}</dt>
                  <dd className={`m-0 font-semibold ${up ? "text-ok" : k === "db" ? "text-danger" : "text-warn"}`}>
                    {up ? `${t("common.online")} · ${c?.latency_ms ?? "–"} ms` : k === "db" ? t("common.down") : t("watch.offline")}
                  </dd>
                </div>
              );
            })}
            <div className="flex items-baseline justify-between border-t border-border-soft py-1.5">
              <dt>{t("home.svcQueue")}</dt>
              <dd className="m-0 tabular-nums text-text-muted">
                {pending === 0 ? t("home.state.idle") : `${pending} ${t("watch.pending")}`}
              </dd>
            </div>
          </dl>
          <p className="m-0 mt-2 text-xs text-text-muted">{t("watch.polled", { s: POLL_MS / 1000 })}</p>
        </div>
      )}
    </div>
  );
}
