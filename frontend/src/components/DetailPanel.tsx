import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { Link } from "react-router-dom";
import { api, type PostDetail, type PostMitigation } from "../lib/api";

export const TECH_COLOR: Record<string, string> = {
  llm_verified: "rgb(167, 139, 250)",
  semantic: "rgb(125, 211, 252)",
  llm_unverified: "rgb(232, 163, 61)",
};

export function DetailPanel({
  id,
  onClose,
}: {
  id: number | null;
  onClose: () => void;
}) {
  const detail = useQuery({
    queryKey: ["post", id],
    queryFn: () => api.post(id!),
    enabled: id !== null,
  });
  return (
    <AnimatePresence>
      {id !== null && (
        <motion.aside
          key={id}
          initial={{ x: "100%" }}
          animate={{ x: 0 }}
          exit={{ x: "100%" }}
          transition={{ duration: 0.3, ease: [0.4, 0, 0.2, 1] }}
          className="fixed top-0 right-0 bottom-0 w-[min(560px,100vw)] z-40 border-l border-border-soft bg-base/95 backdrop-blur-md overflow-y-auto"
        >
          <div className="px-6 py-5">
            <div className="flex items-center justify-between mb-4">
              <span className="font-mono text-[11px] tracking-[0.2em] text-accent">
                POST #{id}
              </span>
              <button
                onClick={onClose}
                className="font-mono text-xs text-text-muted hover:text-accent border border-border-soft px-2 py-1"
              >
                [ CLOSE ]
              </button>
            </div>

            {detail.isLoading && (
              <div className="font-mono text-xs text-text-muted">loading…</div>
            )}
            {detail.data && <DetailBody d={detail.data} />}
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

function DetailBody({ d }: { d: PostDetail }) {
  return (
    <div className="space-y-5">
      <header>
        <div className="font-mono text-[10px] tracking-[0.2em] text-text-muted">
          {d.post.category.toUpperCase()} · {d.post.author}
        </div>
        <h2 className="font-display text-xl mt-1 leading-tight">
          {d.post.thread_title}
        </h2>
        <div className="font-mono text-[10px] text-text-muted mt-1">
          {new Date(d.post.source_created_at * 1000)
            .toISOString()
            .replace("T", " ")
            .slice(0, 19)}
        </div>
      </header>

      {d.analysis?.summary && (
        <section>
          <SectionLabel>LLM SUMMARY</SectionLabel>
          <p className="text-sm leading-relaxed">{d.analysis.summary}</p>
        </section>
      )}

      <section>
        <SectionLabel>BODY</SectionLabel>
        <pre className="font-mono text-xs whitespace-pre-wrap leading-relaxed text-text/90 bg-surface-1/40 border border-border-soft p-3">
          {d.post.body}
        </pre>
      </section>

      {d.techniques.length > 0 && (
        <section>
          <SectionLabel>MITRE TECHNIQUES ({d.techniques.length})</SectionLabel>
          <ul className="space-y-1 font-mono text-xs">
            {d.techniques.map((t) => (
              <li key={t.technique_id + t.source} className="flex gap-2">
                <span
                  className="inline-block w-1.5 h-1.5 rounded-full mt-1.5"
                  style={{ backgroundColor: TECH_COLOR[t.source] }}
                />
                <span className="text-accent">{t.technique_id}</span>
                <span className="text-text">{t.name ?? "—"}</span>
                <span className="ml-auto text-text-muted">
                  {t.source.replace("llm_", "")}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {d.mitigations.length > 0 && (
        <section>
          <SectionLabel>
            DEFENSIVE RECOMMENDATIONS ({d.mitigations.length})
          </SectionLabel>
          <p className="font-mono text-[10px] text-text-muted mb-2 leading-relaxed">
            MITRE ATT&amp;CK mitigations for this post's techniques.
          </p>
          <ul className="space-y-2">
            {d.mitigations.map((m) => (
              <MitigationItem key={m.mitigation_id} m={m} />
            ))}
          </ul>
        </section>
      )}

      {d.iocs.length > 0 && (
        <section>
          <SectionLabel>IOCS ({d.iocs.length})</SectionLabel>
          <ul className="space-y-0.5 font-mono text-xs">
            {d.iocs.map((i, idx) => (
              <li key={idx}>
                <Link
                  to={`/iocs/${encodeURIComponent(i.value)}`}
                  className="inline-flex items-baseline gap-2 px-1 py-0.5 hover:text-accent hover:bg-accent/5 transition-colors"
                  title="pivot on this IOC"
                >
                  <span className="text-text-muted">{i.ioc_type}</span>
                  <span className="text-text break-all">{i.value}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {d.entities.length > 0 && (
        <section>
          <SectionLabel>ENTITIES ({d.entities.length})</SectionLabel>
          <ul className="space-y-0.5 font-mono text-xs">
            {d.entities.map((e, idx) => (
              <li key={idx}>
                <span className="text-text-muted">{e.label}</span>{" "}
                <span className="text-text">{e.text}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function MitigationItem({ m }: { m: PostMitigation }) {
  return (
    <li className="border border-border-soft bg-surface-1/30 px-3 py-2">
      <div className="flex items-baseline gap-2 font-mono text-xs">
        <span className="text-emerald-400">{m.mitigation_id}</span>
        <span className="text-text">{m.name}</span>
        <span
          className="ml-auto text-[10px] text-text-muted"
          title={`counters ${m.addresses.join(", ")}`}
        >
          {m.addresses.join(" ")}
        </span>
      </div>
      <p className="text-[11px] leading-relaxed text-text-muted mt-1 line-clamp-3">
        {m.description}
      </p>
      {m.url && (
        <a
          href={m.url}
          target="_blank"
          rel="noreferrer"
          className="font-mono text-[10px] text-accent hover:underline mt-1 inline-block"
        >
          attack.mitre.org ↗
        </a>
      )}
    </li>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="font-mono text-[10px] tracking-[0.2em] text-text-muted mb-2">
      {children}
    </div>
  );
}
