/** UI translations setup (en/ru/es); only interface text is translated, not data. */

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

// Set up i18next: load the 3 translation files, detect the user's language, remember the choice
void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      ru: { translation: ru },
      es: { translation: es },
    },
    // Missing text falls back to English
    fallbackLng: "en",
    supportedLngs: SUPPORTED_LANGUAGES.map((l) => l.code),
    // React already escapes text, so i18next doesn't need to
    interpolation: { escapeValue: false },
    // Look in localStorage first, then the browser language; save the choice in localStorage
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: "sentinelx.lang",
      caches: ["localStorage"],
    },
  });

export default i18n;
