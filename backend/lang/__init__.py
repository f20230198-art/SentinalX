"""Language layer: detection + translation of scraped post bodies.

Real darknet CTI is heavily non-English — Russian, Chinese, Spanish and Persian
forums carry some of the earliest signal (credential dumps, access sales, 0day
chatter). The original pipeline assumed English: spaCy's en_core_web_sm and the
curated malware/threat-actor keyword pass both silently degrade on Cyrillic.

This package closes that gap. The pipeline detects each post's language, and —
when it isn't English — translates the body to English *before* extraction and
LLM analysis run. Every downstream stage then operates on `body_en`, so
prompts.py and extract.py need no language-awareness of their own.

All offline / free, consistent with the project's no-paid-API constraint:
  * detection — langdetect (pure Python, no model download)
  * translation — argostranslate (offline OPUS-MT models, fetched once per
    language pair and cached locally)
"""

from backend.lang.detect import DetectionResult, detect_language
from backend.lang.translate import TranslationResult, translate_to_english

__all__ = [
    "DetectionResult",
    "detect_language",
    "TranslationResult",
    "translate_to_english",
]
