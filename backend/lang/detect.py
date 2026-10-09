"""Detect a post's language with langdetect (offline, seeded so results repeat)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("sentinelx.lang.detect")

# Below this many characters there isn't enough signal to detect reliably.
MIN_CHARS = 20

# Below this confidence a non-English guess is treated as English (short/IOC-heavy posts get misdetected)
MIN_TRANSLATE_CONFIDENCE = 0.85

# Used when text is too short or detection fails (treated like English)
UNKNOWN = "unknown"

# Map region codes (zh-cn) to plain codes argostranslate understands
_CODE_ALIASES = {
    "zh-cn": "zh",
    "zh-tw": "zh",
}


# Turn "zh-cn" into "zh", leave other codes as they are
def _normalise(code: str) -> str:
    return _CODE_ALIASES.get(code, code)

# langdetect is random by default; we fix its seed once so the same text always gives the same answer
_SEED = 0
_seeded = False


def _ensure_seeded() -> None:
    """Seed langdetect once so detection gives the same result every time"""
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
    """Detection result: lang code, confidence (0-1), is_english."""

    # Detected language code ("en", "ru", ...) and how sure langdetect is (0 to 1)
    lang: str
    confidence: float

    @property
    def is_english(self) -> bool:
        return self.lang == "en"

    @property
    def needs_translation(self) -> bool:
        """True only for a confident non-English detection."""
        return (
            self.lang not in ("en", UNKNOWN)
            and self.confidence >= MIN_TRANSLATE_CONFIDENCE
        )


def detect_language(text: str) -> DetectionResult:
    """Detect the main language of text; returns UNKNOWN on failure (never raises)."""
    # Too little text to judge -> unknown
    stripped = (text or "").strip()
    if len(stripped) < MIN_CHARS:
        return DetectionResult(UNKNOWN, 0.0)

    # Make sure results are repeatable, then load langdetect
    _ensure_seeded()
    try:
        from langdetect import detect_langs
    except ImportError:
        log.warning("langdetect not installed — language detection disabled "
                    "(pip install langdetect)")
        return DetectionResult(UNKNOWN, 0.0)

    # Ask langdetect for its ranked guesses
    try:
        ranked = detect_langs(stripped)
    except Exception as e:  # langdetect raises LangDetectException on no-features
        log.debug("detect failed (%s) — treating as unknown", e)
        return DetectionResult(UNKNOWN, 0.0)

    if not ranked:
        return DetectionResult(UNKNOWN, 0.0)

    # Take the top guess
    top = ranked[0]
    return DetectionResult(_normalise(top.lang), float(top.prob))
