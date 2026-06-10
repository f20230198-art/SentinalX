"""Offline translation of non-English post bodies into English.

Built on `argostranslate` — open-source, fully offline neural MT (OPUS-MT
models). No API key, no network at translate-time once the language package is
installed. This keeps the project's "no paid API" rule intact while still
giving the LLM and spaCy English text to work with.

Why translate *before* extraction instead of relying on Mistral's native
multilingual ability:
  * spaCy's en_core_web_sm produces near-garbage NER on Cyrillic / CJK text.
  * The curated MALWARE / THREAT_ACTOR keyword pass is English-only.
  * Mistral 7B *can* read Russian, but its English summaries of Russian input
    are noticeably weaker than its summaries of (translated) English input.
Translating once, up front, lets every downstream stage stay monolingual.

Model lifecycle:
  * argostranslate packages are per-direction (ru->en, zh->en, …), ~100 MB
    each, downloaded once and cached under the user's argos data dir.
  * `_ensure_package(from_code)` installs the needed package on first use. If
    the install fails (offline, no package for that language), translation
    degrades gracefully: the original body is returned untranslated and the
    result is flagged `ok=False`. The pipeline then runs extraction on the
    original text — degraded, but never broken.

IOC safety:
  argostranslate occasionally mangles long alphanumeric tokens (hashes, BTC
  addresses). It doesn't matter for *our* IOC extraction, because the extraction
  step runs IOC regexes against the ORIGINAL body, not the translation — see
  pipeline/run.py. The translation feeds NER + the LLM only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("sentinelx.lang.translate")

# Languages we proactively support. argostranslate can do more; this list just
# controls which packages we *auto-install*. Anything outside it still gets a
# best-effort attempt via _ensure_package.
SUPPORTED_SOURCE_LANGS = ("ru", "zh", "es", "fa", "de", "fr", "uk", "pt", "ar")

# Translation is chunked: argostranslate handles long input but quality and
# memory both improve with paragraph-sized chunks. The seed corpus tops out
# around 2k chars so this rarely splits, but it's a safety belt for big posts.
MAX_CHUNK_CHARS = 1500

# In-process cache of which source languages we've already (tried to) install,
# so a watch-mode loop doesn't re-probe the package index every batch.
_install_attempted: set[str] = set()
_install_ok: set[str] = set()


@dataclass(frozen=True)
class TranslationResult:
    """Outcome of translating one body.

    text        English text — the translation, or the original on failure.
    ok          True if a real translation happened; False if degraded
                (no package / library missing / error) and `text` is the
                untouched original.
    source_lang The ISO code we translated from (echoed for the caller).
    """

    text: str
    ok: bool
    source_lang: str


def _ensure_package(from_code: str) -> bool:
    """Install the from_code->en argostranslate package if not already present.

    Returns True if a usable package is available afterwards. Result is memoised
    per language for the lifetime of the process.
    """
    if from_code in _install_attempted:
        return from_code in _install_ok
    _install_attempted.add(from_code)

    try:
        import argostranslate.package
        import argostranslate.translate
    except ImportError:
        log.warning("argostranslate not installed — translation disabled "
                    "(pip install argostranslate)")
        return False

    # Already installed from a previous run?
    installed = argostranslate.translate.get_installed_languages()
    have_from = any(lg.code == from_code for lg in installed)
    have_en = any(lg.code == "en" for lg in installed)
    if have_from and have_en:
        _install_ok.add(from_code)
        return True

    # Not installed — fetch the package index and install the matching one.
    try:
        argostranslate.package.update_package_index()
        available = argostranslate.package.get_available_packages()
        pkg = next(
            (p for p in available
             if p.from_code == from_code and p.to_code == "en"),
            None,
        )
        if pkg is None:
            log.warning("no argostranslate package for %s->en", from_code)
            return False
        log.info("installing argostranslate package %s->en (one-time, ~100MB)",
                 from_code)
        argostranslate.package.install_from_path(pkg.download())
    except Exception as e:  # network down, disk full, index unreachable …
        log.warning("could not install %s->en package: %s", from_code, e)
        return False

    _install_ok.add(from_code)
    return True


def _chunks(text: str, n: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split text into <=n-char chunks, preferring paragraph boundaries."""
    if len(text) <= n:
        return [text]
    out: list[str] = []
    buf = ""
    for para in text.split("\n"):
        if len(buf) + len(para) + 1 > n and buf:
            out.append(buf)
            buf = ""
        buf = f"{buf}\n{para}" if buf else para
        # A single paragraph longer than n: hard-split it.
        while len(buf) > n:
            out.append(buf[:n])
            buf = buf[n:]
    if buf:
        out.append(buf)
    return out


def translate_to_english(text: str, source_lang: str) -> TranslationResult:
    """Translate `text` from `source_lang` into English.

    Never raises. If `source_lang` is already 'en' the original is returned with
    ok=True (nothing to do). If translation can't be performed — missing
    library, no language package, runtime error — the original text is returned
    with ok=False so the caller can record the degradation and still proceed.
    """
    body = text or ""
    if source_lang == "en" or not body.strip():
        return TranslationResult(body, True, source_lang)

    if not _ensure_package(source_lang):
        return TranslationResult(body, False, source_lang)

    try:
        import argostranslate.translate

        translated = [
            argostranslate.translate.translate(chunk, source_lang, "en")
            for chunk in _chunks(body)
        ]
        return TranslationResult("\n".join(translated), True, source_lang)
    except Exception as e:
        log.warning("translation %s->en failed: %s", source_lang, e)
        return TranslationResult(body, False, source_lang)
