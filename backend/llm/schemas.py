"""Pydantic schemas that check the LLM's JSON replies before saving (bad shape rejected, values clamped/trimmed)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.llm.prompts import INTENT_LABELS

# Limits so a bad reply can't flood the DB
MAX_LIST = 20
MAX_STR = 300
# Valid technique ID shape: T1234 or T1234.001
_TCODE_RE = re.compile(r"^T\d{4}(?:\.\d{3})?$")


# Trim and cap a string
def _short(s: Any) -> str:
    return str(s).strip()[:MAX_STR]


# Shape of the intent reply
class IntentOut(BaseModel):
    intent: str
    confidence: float = 0.5
    reason: str = ""

    # Intent must be one of our allowed labels
    @field_validator("intent", mode="before")
    @classmethod
    def _label(cls, v: Any) -> str:
        label = str(v).strip().lower()
        if label not in INTENT_LABELS:
            raise ValueError(f"intent {label!r} not in {INTENT_LABELS}")
        return label

    # Confidence forced into 0..1 (falls back to 0.5)
    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v: Any) -> float:
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.5

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, v: Any) -> str:
        return _short(v or "")


# Turn the value into a clean, capped list of strings
def _str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        raise ValueError("expected a list")
    return [_short(x) for x in v if isinstance(x, (str, int, float)) and str(x).strip()][:MAX_LIST]


# Shape of the targets reply
class TargetsOut(BaseModel):
    industries: list[str] = Field(default_factory=list)
    geographies: list[str] = Field(default_factory=list)
    victim_types: list[str] = Field(default_factory=list)

    @field_validator("industries", "geographies", "victim_types", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> list[str]:
        return _str_list(v)


# One suggested technique
class TechniqueItem(BaseModel):
    id: str
    name: str = ""
    evidence: str = ""

    # ID must look like a real T-code
    @field_validator("id", mode="before")
    @classmethod
    def _tcode(cls, v: Any) -> str:
        s = str(v).strip().upper()
        if not _TCODE_RE.match(s):
            raise ValueError(f"not a T-code: {s!r}")
        return s

    @field_validator("name", "evidence", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str:
        return _short(v or "")


# Shape of the techniques reply
class TechniquesOut(BaseModel):
    techniques: list[TechniqueItem] = Field(default_factory=list)
    behaviour: list[str] = Field(default_factory=list)

    @field_validator("techniques", mode="before")
    @classmethod
    def _items(cls, v: Any) -> list:
        # Drop only bad items instead of rejecting the whole reply
        if not isinstance(v, list):
            return []
        good = []
        for it in v[:MAX_LIST]:
            if isinstance(it, str):
                it = {"id": it}
            try:
                good.append(TechniqueItem.model_validate(it))
            except ValidationError:
                continue
        return good

    @field_validator("behaviour", mode="before")
    @classmethod
    def _beh(cls, v: Any) -> list[str]:
        return _str_list(v)


# Which schema checks which prompt's reply
SCHEMAS: dict[str, type[BaseModel]] = {
    "intent": IntentOut,
    "targets": TargetsOut,
    "techniques": TechniquesOut,
}


def validate(name: str, data: dict | None) -> tuple[dict | None, str | None]:
    """Validate one parsed LLM response. Returns (clean_dict, error)."""
    # Reply wasn't JSON at all, or wasn't a JSON object
    if data is None:
        return None, "unparseable JSON"
    if not isinstance(data, dict):
        return None, f"expected a JSON object, got {type(data).__name__}"
    # Check against the schema and return the cleaned data
    try:
        return SCHEMAS[name].model_validate(data).model_dump(), None
    except ValidationError as e:
        return None, f"schema validation failed: {e.errors()[0]['msg']}"
