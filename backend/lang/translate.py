"""Offline translation of non-English posts to English (argostranslate).
IOCs are extracted from the original text, so translation mistakes don't affect them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("sentinelx.lang.translate")

# Languages we auto-install translation models for
SUPPORTED_SOURCE_LANGS = ("ru", "zh", "es", "fa", "de", "fr", "uk", "pt", "ar")

# Translate in chunks of this size (better quality, less memory)
MAX_CHUNK_CHARS = 1500

# Languages we already tried to install (don't retry every batch)
# Languages whose model is installed and working
_install_attempted: set[str] = set()
_install_ok: set[str] = set()


@dataclass(frozen=True)
class TranslationResult:
    """Translation result: text (English or original), ok (translated?), source_lang."""

    text: str
    ok: bool
    source_lang: str


def _ensure_package(from_code: str) -> bool:
    """Install the <lang>->en model if missing; True if usable (cached per process)."""
    # Already tried this language? Reuse the earlier answer
    if from_code in _install_attempted:
        return from_code in _install_ok
    _install_attempted.add(from_code)

    # Translation library missing -> can't translate
    try:
        import argostranslate.package
        import argostranslate.translate
    except ImportError:
        log.warning("argostranslate not installed — translation disabled "
                    "(pip install argostranslate)")
        return False

    # Model already on disk from an earlier run?
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

    # Remember it works so we skip these checks next time
    _install_ok.add(from_code)
    return True


def _chunks(text: str, n: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split text into <=n-char chunks, preferring paragraph boundaries."""
    # Short text -> one chunk
    if len(text) <= n:
        return [text]
    out: list[str] = []
    buf = ""
    # Build chunks paragraph by paragraph; start a new chunk when the current one would get too long
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
    """Translate text to English; on failure returns the original with ok=False."""
    # Nothing to translate (already English or empty)
    body = text or ""
    if source_lang == "en" or not body.strip():
        return TranslationResult(body, True, source_lang)

    # No model for this language -> return the original text
    if not _ensure_package(source_lang):
        return TranslationResult(body, False, source_lang)

    # Translate each chunk and join them back together
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
