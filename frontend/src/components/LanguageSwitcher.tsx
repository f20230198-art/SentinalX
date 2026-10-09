/** Language picker in the header (choice is saved in localStorage). */

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
      {/* One button per language; clicking it switches the whole UI */}
      {SUPPORTED_LANGUAGES.map((lng) => {
        const isActive = lng.code === active;
        return (
          <button
            key={lng.code}
            onClick={() => void i18n.changeLanguage(lng.code)}
            title={lng.name}
            aria-pressed={isActive}
            className={`px-2 py-1 text-xs font-semibold transition-colors ${
              isActive
                ? "bg-text text-surface-1"
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
