import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { motion, AnimatePresence } from "framer-motion";
import { Link } from "react-router-dom";
import { api, type PostDetail, type PostMitigation } from "../lib/api";
import { ProvenanceLegend, ProvenanceMark, QueryError } from "./Evidence";
import { PROVENANCE, type Provenance } from "../lib/palette";

// Side panel that slides in from the right and shows everything about one post
export function DetailPanel({
  id,
  onClose,
}: {
  id: number | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  // Load the post's details (only when a post is selected)
  const detail = useQuery({
    queryKey: ["post", id],
    queryFn: () => api.post(id!),
    enabled: id !== null,
  });
  // Esc closes the panel from anywhere.
  useEffect(() => {
    if (id === null) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [id, onClose]);
  return (
    <AnimatePresence>
      {/* Slide in when a post is selected, slide out when closed */}
      {id !== null && (
        <motion.aside
          key={id}
          initial={{ x: "100%" }}
          animate={{ x: 0 }}
          exit={{ x: "100%" }}
          transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
          role="dialog"
          aria-label={t("post.title", { id })}
          className="fixed top-0 right-0 bottom-0 z-40 w-[min(560px,100vw)] overflow-y-auto border-l-2 border-rule bg-surface-1 shadow-[-12px_0_32px_rgba(0,0,0,0.5)]"
        >
          <div className="px-6 py-5">
            {/* Top row: post number + close button */}
            <div className="flex items-center justify-between mb-4">
              <span className="font-mono text-sm text-text-muted">
                {t("post.title", { id })}
              </span>
              <button
                onClick={onClose}
                className="border border-text px-2.5 py-1 text-sm font-semibold hover:bg-text hover:text-surface-1"
              >
                {t("common.close")} <span className="font-normal text-text-muted">Esc</span>
              </button>
            </div>

            {/* Loading, error, or the post itself */}
            {detail.isLoading && (
              <p className="text-sm text-text-muted">{t("common.loading")}</p>
            )}
            {detail.isError && (
              <QueryError what={`post #${id}`} error={detail.error} onRetry={() => detail.refetch()} />
            )}
            {detail.data && <DetailBody d={detail.data} />}
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

// The panel's content, section by section
function DetailBody({ d }: { d: PostDetail }) {
  const { t } = useTranslation();
  // Translated = has an English translation and wasn't English
  const isTranslated = !!d.post.body_en && d.post.lang !== "en";
  return (
    <div className="space-y-5">
      {/* Category, author, translated badge, title, date */}
      <header>
        <div className="flex items-center gap-2 text-sm text-text-muted">
          <span>
            {d.post.category} · {d.post.author}
          </span>
          {isTranslated && <LanguageBadge lang={d.post.lang} />}
        </div>
        <h2 className="font-display text-xl mt-1 leading-tight">
          {d.post.thread_title}
        </h2>
        <div className="font-mono text-xs text-text-muted mt-1">
          {new Date(d.post.source_created_at * 1000)
            .toISOString()
            .replace("T", " ")
            .slice(0, 19)}
        </div>
      </header>

      {/* LLM summary */}
      {d.analysis?.summary && (
        <section>
          <SectionLabel>{t("post.llmSummary")}</SectionLabel>
          <p className="text-sm leading-relaxed">{d.analysis.summary}</p>
        </section>
      )}

      {/* Post text (translation toggle if non-English) */}
      <PostBody
        body={d.post.body}
        bodyEn={d.post.body_en}
        lang={d.post.lang}
        isTranslated={isTranslated}
      />

      {/* MITRE techniques, each with where the match came from */}
      {d.techniques.length > 0 && (
        <section>
          <SectionLabel>
            {t("post.mitreTechniques", { count: d.techniques.length })}
          </SectionLabel>
          <div className="mb-3">
            <ProvenanceLegend compact />
          </div>
          <ul className="m-0 list-none p-0 text-sm">
            {d.techniques.map((tq) => (
              <li
                key={tq.technique_id + tq.source}
                className="flex items-center gap-3 border-t border-border-soft py-1.5"
                title={PROVENANCE[tq.source as Provenance]?.description}
              >
                <ProvenanceMark source={tq.source} />
                <span className="w-20 font-mono">{tq.technique_id}</span>
                {tq.name ? (
                  <span className="font-semibold">{tq.name}</span>
                ) : (
                  <span className="text-warn">Not in ATT&amp;CK corpus</span>
                )}
                <span className="ml-auto text-xs text-text-muted tabular-nums">
                  {tq.score != null ? `cos ${tq.score.toFixed(2)}` : PROVENANCE[tq.source as Provenance]?.short}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Recommended defences */}
      {d.mitigations.length > 0 && (
        <section>
          <SectionLabel>
            {t("post.defensiveRecommendations", { count: d.mitigations.length })}
          </SectionLabel>
          <p className="font-mono text-xs text-text-muted mb-2 leading-relaxed">
            {t("post.mitigationsNote")}
          </p>
          <ul className="space-y-2">
            {d.mitigations.map((m) => (
              <MitigationItem key={m.mitigation_id} m={m} />
            ))}
          </ul>
        </section>
      )}

      {/* IOCs: click one to open its pivot page */}
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

      {/* Named entities */}
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

/** Language name for an ISO code (falls back to the code itself). */
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
      className="inline-flex items-center gap-1 border border-accent/40 text-accent px-1.5 py-0.5 text-xs"
      title={languageName(lang)}
    >
      <span>Translated {(lang ?? "??").toUpperCase()}–EN</span>
    </span>
  );
}

/** Post body: shows the English translation by default, with a toggle to the original. */
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
        <pre className="m-0 whitespace-pre-wrap border border-border-soft bg-surface-2 p-3 font-sans text-sm leading-relaxed text-text">
          {body}
        </pre>
      </section>
    );
  }

  // Show the translation unless the user switched to the original
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
          className="font-mono text-xs text-text-muted hover:text-accent border border-border-soft px-2 py-0.5"
        >
          {showOriginal ? t("post.showTranslation") : t("post.showOriginal")}
        </button>
      </div>
      {!showOriginal && (
        <p className="font-mono text-xs text-text-muted mb-2 leading-relaxed">
          {t("post.translatedNote", { language: languageName(lang) })}
        </p>
      )}
      <pre className="m-0 whitespace-pre-wrap border border-border-soft bg-surface-2 p-3 font-sans text-sm leading-relaxed text-text">
        {displayed}
      </pre>
    </section>
  );
}

// One mitigation: id, name, which techniques it counters, description, link
function MitigationItem({ m }: { m: PostMitigation }) {
  return (
    <li className="border border-border-soft bg-surface-1/30 px-3 py-2">
      <div className="flex items-baseline gap-2 font-mono text-xs">
        <span className="font-mono text-ok">{m.mitigation_id}</span>
        <span className="font-semibold text-text">{m.name}</span>
        <span
          className="ml-auto text-xs text-text-muted"
          title={`counters ${m.addresses.join(", ")}`}
        >
          {m.addresses.join(" ")}
        </span>
      </div>
      <p className="text-xs leading-relaxed text-text-muted mt-1 line-clamp-3">
        {m.description}
      </p>
      {m.url && (
        <a
          href={m.url}
          target="_blank"
          rel="noreferrer"
          className="font-mono text-xs text-accent hover:underline mt-1 inline-block"
        >
          attack.mitre.org
        </a>
      )}
    </li>
  );
}

// Small heading used by every section in the panel
function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="m-0 mb-2 border-t-2 border-rule pt-2 text-sm font-bold">
      {children}
    </h3>
  );
}
