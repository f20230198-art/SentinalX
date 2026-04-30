"""Fetch and parse the official MITRE ATT&CK Enterprise STIX 2.1 corpus.

The corpus is a single ~30 MB JSON file in the public mitre/cti GitHub mirror.
We cache it under data/mitre/ so re-runs don't re-download. We extract only
attack-pattern objects (techniques + sub-techniques), drop revoked/deprecated
ones, normalise to a flat record shape, and return the list. Embedding +
persistence happens in embed.py / run.py.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MITRE_URL = (
    "https://raw.githubusercontent.com/mitre/cti/master/"
    "enterprise-attack/enterprise-attack.json"
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE = REPO_ROOT / "data" / "mitre" / "enterprise-attack.json"


@dataclass(frozen=True)
class Technique:
    technique_id: str
    name: str
    description: str
    tactics: tuple[str, ...]
    url: str | None
    is_subtechnique: bool
    parent_id: str | None


def download(cache_path: Path = DEFAULT_CACHE, force: bool = False) -> Path:
    """Download the corpus JSON to cache_path. Skips if file already exists."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and not force:
        return cache_path
    with urllib.request.urlopen(MITRE_URL, timeout=120) as resp:
        data = resp.read()
    cache_path.write_bytes(data)
    return cache_path


def _external_id_and_url(obj: dict) -> tuple[str | None, str | None]:
    for ref in obj.get("external_references", []):
        if ref.get("source_name") == "mitre-attack":
            return ref.get("external_id"), ref.get("url")
    return None, None


def parse(cache_path: Path = DEFAULT_CACHE) -> list[Technique]:
    """Parse cached STIX JSON into a flat list of Technique records.

    Filters: only attack-pattern, drop revoked/deprecated, must have an
    mitre-attack external_id (T-code).
    """
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    objects = raw.get("objects", [])

    # Build a STIX-id -> T-code map for parent lookup of sub-techniques.
    stix_to_tcode: dict[str, str] = {}
    for o in objects:
        if o.get("type") != "attack-pattern":
            continue
        if o.get("revoked") or o.get("x_mitre_deprecated"):
            continue
        tcode, _ = _external_id_and_url(o)
        if tcode:
            stix_to_tcode[o["id"]] = tcode

    # Sub-technique relationships are recorded as relationship objects with
    # relationship_type == 'subtechnique-of'. Map child STIX id -> parent STIX id.
    sub_parent: dict[str, str] = {}
    for o in objects:
        if o.get("type") == "relationship" and o.get("relationship_type") == "subtechnique-of":
            child = o.get("source_ref")
            parent = o.get("target_ref")
            if child and parent:
                sub_parent[child] = parent

    out: list[Technique] = []
    for o in objects:
        if o.get("type") != "attack-pattern":
            continue
        if o.get("revoked") or o.get("x_mitre_deprecated"):
            continue
        tcode, url = _external_id_and_url(o)
        if not tcode:
            continue
        tactics = tuple(
            phase.get("phase_name", "")
            for phase in o.get("kill_chain_phases", [])
            if phase.get("kill_chain_name") == "mitre-attack"
        )
        is_sub = bool(o.get("x_mitre_is_subtechnique"))
        parent_stix = sub_parent.get(o["id"])
        parent_tcode = stix_to_tcode.get(parent_stix) if parent_stix else None
        out.append(
            Technique(
                technique_id=tcode,
                name=o.get("name", ""),
                description=o.get("description", ""),
                tactics=tactics,
                url=url,
                is_subtechnique=is_sub,
                parent_id=parent_tcode,
            )
        )
    return out


def fetch_and_parse(cache_path: Path = DEFAULT_CACHE, force_download: bool = False) -> list[Technique]:
    download(cache_path, force=force_download)
    return parse(cache_path)
