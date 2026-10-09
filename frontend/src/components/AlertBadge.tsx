import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

/** Header link to Alerts; shows the unseen count when there is one. */
export function AlertBadge() {
  // Re-check unseen alerts every 20 seconds
  const q = useQuery({ queryKey: ["alerts", null], queryFn: () => api.alerts(), refetchInterval: 20_000 });
  const n = q.data?.unseen ?? 0;
  return (
    <Link
      to="/alerts"
      className={`flex items-center gap-1.5 border px-2.5 py-1 text-xs font-semibold no-underline ${
        n > 0 ? "border-accent bg-accent text-surface-1" : "border-border-soft text-text-muted hover:border-text hover:text-text"
      }`}
      aria-label={n > 0 ? `${n} unseen alerts` : "Alerts"}
    >
      Alerts
      {n > 0 && <span className="tabular-nums">{n}</span>}
    </Link>
  );
}
