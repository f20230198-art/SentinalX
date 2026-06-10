import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
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
                {t("post.title", { id })}
              </span>
              <button
                onClick={onClose}
                className="font-mono text-xs text-text-muted hover:text-accent border border-border-soft px-2 py-1"
              >
                [ {t("common.close")} ]
              </button>
            </div>

            {detail.isLoading && (
              <div className="font-mono text-xs text-text-muted">
                {t("common.loading")}
              </div>
            )}
            {detail.data && <DetailBody d={detail.data} />}
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

function DetailBody({ d }: { d: PostDetail }) {
  const { t } = useTranslation();
  // A post is "translated" when the backend stored an English translation —
  // i.e. it was non-English. body_en is null for English posts (and for posts
  // ingested before multilingual support landed).
  const isTranslated = !!d.post.body_en && d.post.lang !== "en";
  return (
    <div className="space-y-5">
      <header>
        <div className="font-mono text-[10px] tracking-[0.2em] text-text-muted flex items-center gap-2">
          <span>
            {d.post.category.toUpperCase()} · {d.post.author}
          </span>
          {isTranslated && <LanguageBadge lang={d.post.lang} />}
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
          <SectionLabel>{t("post.llmSummary")}</SectionLabel>
          <p className="text-sm leading-relaxed">{d.analysis.summary}</p>
        </section>
      )}

      <PostBody
        body={d.post.body}
        bodyEn={d.post.body_en}
        lang={d.post.lang}
        isTranslated={isTranslated}
      />

      {d.techniques.length > 0 && (
        <section>
          <SectionLabel>
            {t("post.mitreTechniques", { count: d.techniques.length })}
          </SectionLabel>
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
            {t("post.defensiveRecommendations", { count: d.mitigations.length })}
          </SectionLabel>
          <p className="font-mono text-[10px] text-text-muted mb-2 leading-relaxed">
            {t("post.mitigationsNote")}
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
          <SectionLabel>{t("post.iocs", { count: d.iocs.length })}</SectionLabel>
          <ul className="space-y-0.5 font-mono text-xs">
            {d.iocs.map((i, idx) => (
              <li key={idx}>
                <Link
                  to={`/iocs/${encodeURIComponent(i.value)}`}
                  className="inline-flex items-baseline gap-2 px-1 py-0.5 hover:text-accent hover:bg-accent/5 transition-colors"
                  title={t("post.pivotHint")}
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
          <SectionLabel>
            {t("post.entities", { count: d.entities.length })}
          </SectionLabel>
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

/** Human-readable name for an ISO language code, via the i18n catalog.
 *  Falls back to the upper-cased code for languages not in the catalog. */
function useLanguageName() {
  const { t } = useTranslation();
  return (lang: string | null): string => {
    if (!lang) return t("language.unknown");
    const key = `language.${lang}`;
    const name = t(key);
    return name === key ? lang.toUpperCase() : name;
  };
}

/** Small pill in the post header marking a translated (non-English) post. */
function LanguageBadge({ lang }: { lang: string | null }) {
  const languageName = useLanguageName();
  return (
    <span
      className="inline-flex items-center gap-1 border border-accent/40 text-accent px-1.5 py-0.5 text-[9px] tracking-[0.15em]"
      title={languageName(lang)}
    >
      <span>⇄</span>
      <span>{(lang ?? "??").toUpperCase()} → EN</span>
    </span>
  );
}

/** Post body section. For a translated post it shows the English translation
 *  by default with a toggle to the original-language source; the LLM analysis
 *  ran on the English text, so that is the more useful default view. */
function PostBody({
  body,
  bodyEn,
  lang,
  isTranslated,
}: {
  body: string;
  bodyEn: string | null;
  lang: string | null;
  isTranslated: boolean;
}) {
  const { t } = useTranslation();
  const languageName = useLanguageName();
  const [showOriginal, setShowOriginal] = useState(false);

  // English (or legacy) post: render the body plainly, no toggle.
  if (!isTranslated || !bodyEn) {
    return (
      <section>
        <SectionLabel>{t("post.body")}</SectionLabel>
        <pre className="font-mono text-xs whitespace-pre-wrap leading-relaxed text-text/90 bg-surface-1/40 border border-border-soft p-3">
          {body}
        </pre>
      </section>
    );
  }

  const displayed = showOriginal ? body : bodyEn;
  return (
    <section>
      <div className="flex items-center justify-between mb-2">
        <SectionLabel>
          {showOriginal
            ? t("post.bodyOriginal", { lang: (lang ?? "??").toUpperCase() })
            : t("post.bodyTranslated")}
        </SectionLabel>
        <button
          onClick={() => setShowOriginal((v) => !v)}
          className="font-mono text-[10px] text-text-muted hover:text-accent border border-border-soft px-2 py-0.5"
        >
          {showOriginal ? t("post.showTranslation") : t("post.showOriginal")}
        </button>
      </div>
      {!showOriginal && (
        <p className="font-mono text-[10px] text-text-muted mb-2 leading-relaxed">
          {t("post.translatedNote", { language: languageName(lang) })}
        </p>
      )}
      <pre className="font-mono text-xs whitespace-pre-wrap leading-relaxed text-text/90 bg-surface-1/40 border border-border-soft p-3">
        {displayed}
      </pre>
    </section>
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
