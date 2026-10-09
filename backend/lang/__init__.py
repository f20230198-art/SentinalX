"""Language layer: detect each post's language and translate non-English to English (offline)."""

from backend.lang.detect import DetectionResult, detect_language
from backend.lang.translate import TranslationResult, translate_to_english

__all__ = [
    "DetectionResult",
    "detect_language",
    "TranslationResult",
    "translate_to_english",
]
