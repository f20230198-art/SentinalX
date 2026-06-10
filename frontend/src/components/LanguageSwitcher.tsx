/**
 * Language switcher for the console header.
 *
 * A compact segmented control matching the terminal aesthetic — the same
 * bracketed-mono style as the [ CRT ] toggle next to it. Selecting a language
 * calls i18next; the choice is persisted to localStorage by the detector
 * configured in src/i18n/index.ts, so it survives reloads.
 */

import { useTranslation } from "react-i18next";
import { SUPPORTED_LANGUAGES } from "../i18n";

export function LanguageSwitcher() {
  const { i18n, t } = useTranslation();
  // i18n.language can carry a region suffix ("en-US"); compare on the base.
  const active = i18n.language.split("-")[0];

  return (
    <div
      className="flex items-center border border-border-soft"
      role="group"
      aria-label={t("common.language")}
    >
      {SUPPORTED_LANGUAGES.map((lng) => {
        const isActive = lng.code === active;
        return (
          <button
            key={lng.code}
            onClick={() => void i18n.changeLanguage(lng.code)}
            title={lng.name}
            aria-pressed={isActive}
            className={`font-mono text-[10px] tracking-[0.2em] px-2.5 py-1.5 transition-colors ${
              isActive
                ? "text-accent bg-accent/10"
                : "text-text-muted hover:text-text"
            }`}
          >
            {lng.label}
          </button>
        );
      })}
    </div>
  );
}
