"""Language detection over scraped post bodies.

Built on `langdetect` — a pure-Python port of Google's language-detection
library. No model download, no native deps; it ships its profiles in the wheel.
That keeps it consistent with the project's "free / local / no surprises"
constraint and makes it cheap enough to run on every post.

Design notes:
  * langdetect is non-deterministic by default (it samples). We seed its PRNG
    once at import so the same body always yields the same language — important
    for reproducible re-runs and for tests.
  * Short or IOC-only bodies ("bc1q… 1.2.3.4") have no real linguistic content;
    langdetect will still guess, often wrongly. We treat anything below
    MIN_CHARS as 'unknown' with zero confidence rather than trusting a coin
    flip. Callers treat 'unknown' the same as 'en' — no translation attempted.
  * ISO 639-1 two-letter codes throughout ('en', 'ru', 'zh', 'es', …), which is
    also what argostranslate's `from_code` expects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("sentinelx.lang.detect")

# Below this many characters there isn't enough signal to detect reliably.
MIN_CHARS = 20

# Minimum probability to trust a *non-English* detection enough to translate.
# Darknet posts are jargon- and IOC-heavy: a short reply that is mostly a BTC
# address or a hash has almost no natural-language signal, and langdetect will
# still return a guess — empirically a wrong one around 0.5-0.6 confidence
# ("APT41 affiliated?" detected as Danish, etc.). Genuine Russian/Spanish posts
# detect at ~0.99. A 0.85 gate cleanly separates the two: below it we treat the
# post as English (no translation) rather than round-tripping English text
# through an MT model, which can corrupt it.
MIN_TRANSLATE_CONFIDENCE = 0.85

# Sentinel for "not enough text" / "detector failed". Callers must treat this
# as non-translatable (same handling as English).
UNKNOWN = "unknown"

# langdetect emits a few region-tagged codes ('zh-cn', 'zh-tw') that
# argostranslate's `from_code` doesn't recognise — it wants the bare ISO 639-1
# code. Normalise them here so detection output feeds straight into translation.
_CODE_ALIASES = {
    "zh-cn": "zh",
    "zh-tw": "zh",
}


def _normalise(code: str) -> str:
    return _CODE_ALIASES.get(code, code)

_SEED = 0
_seeded = False


def _ensure_seeded() -> None:
    """Seed langdetect's PRNG once so detection is deterministic."""
    global _seeded
    if _seeded:
        return
    try:
        from langdetect import DetectorFactory

        DetectorFactory.seed = _SEED
        _seeded = True
    except ImportError:
        # Surfaced properly on the first detect() call; nothing to do here.
        pass


@dataclass(frozen=True)
class DetectionResult:
    """Outcome of detecting one body's language.

    lang        ISO 639-1 code, or UNKNOWN when undetectable.
    confidence  0..1 probability from langdetect, or 0.0 for UNKNOWN.
    is_english  Convenience: True when no translation is needed.
    """

    lang: str
    confidence: float

    @property
    def is_english(self) -> bool:
        return self.lang == "en"

    @property
    def needs_translation(self) -> bool:
        """True only when we have a *confident* non-English detection.

        A low-confidence non-English guess (typical of short, IOC-heavy posts)
        is treated as English — see MIN_TRANSLATE_CONFIDENCE. This keeps the
        pipeline from translating English text it merely misread.
        """
        return (
            self.lang not in ("en", UNKNOWN)
            and self.confidence >= MIN_TRANSLATE_CONFIDENCE
        )


def detect_language(text: str) -> DetectionResult:
    """Detect the dominant language of `text`.

    Never raises — a detection failure (empty text, exotic script langdetect
    can't profile, missing dependency) degrades to UNKNOWN, which the pipeline
    treats as "leave as-is, don't translate".
    """
    stripped = (text or "").strip()
    if len(stripped) < MIN_CHARS:
        return DetectionResult(UNKNOWN, 0.0)

    _ensure_seeded()
    try:
        from langdetect import detect_langs
    except ImportError:
        log.warning("langdetect not installed — language detection disabled "
                    "(pip install langdetect)")
        return DetectionResult(UNKNOWN, 0.0)

    try:
        ranked = detect_langs(stripped)
    except Exception as e:  # langdetect raises LangDetectException on no-features
        log.debug("detect failed (%s) — treating as unknown", e)
        return DetectionResult(UNKNOWN, 0.0)

    if not ranked:
        return DetectionResult(UNKNOWN, 0.0)

    top = ranked[0]
    return DetectionResult(_normalise(top.lang), float(top.prob))
