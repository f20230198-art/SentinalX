/**
 * i18n bootstrap for the SentinelX console.
 *
 * The backend is multilingual — it detects + translates scraped darknet posts.
 * This makes the *interface* multilingual too, so an analyst can drive the
 * console in their own language.
 *
 * Scope note: we translate UI chrome — nav, section labels, buttons, status
 * words, helper prose. We deliberately do NOT translate:
 *   - the terminal-aesthetic index tags ("[01]", "[02] FEED" …) — decorative,
 *     and part of the product's identity;
 *   - data from the API (post bodies, MITRE technique names, IOC values) —
 *     those are content, handled separately (post bodies via body_en).
 *
 * Language is persisted to localStorage and also honours the browser's
 * Accept-Language on first visit, via i18next-browser-languagedetector.
 */

import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import en from "./locales/en.json";
import ru from "./locales/ru.json";
import es from "./locales/es.json";

/** Languages offered in the UI switcher. `code` must match a locale file. */
export const SUPPORTED_LANGUAGES = [
  { code: "en", label: "EN", name: "English" },
  { code: "ru", label: "RU", name: "Русский" },
  { code: "es", label: "ES", name: "Español" },
] as const;

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]["code"];

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      ru: { translation: ru },
      es: { translation: es },
    },
    fallbackLng: "en",
    supportedLngs: SUPPORTED_LANGUAGES.map((l) => l.code),
    // Missing keys fall through to the English string rather than showing the
    // raw key — a half-translated locale still reads cleanly.
    interpolation: { escapeValue: false },
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: "sentinelx.lang",
      caches: ["localStorage"],
    },
  });

export default i18n;
