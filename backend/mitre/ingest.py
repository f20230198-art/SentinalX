"""Download (cached) and parse the MITRE ATT&CK technique data."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Official MITRE ATT&CK data file (published on GitHub)
MITRE_URL = (
    "https://raw.githubusercontent.com/mitre/cti/master/"
    "enterprise-attack/enterprise-attack.json"
)

# Where the downloaded file is kept so we only download once
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE = REPO_ROOT / "data" / "mitre" / "enterprise-attack.json"


# One MITRE technique, e.g. T1566 Phishing
@dataclass(frozen=True)
class Technique:
    technique_id: str
    name: str
    description: str
    tactics: tuple[str, ...]
    url: str | None
    is_subtechnique: bool
    parent_id: str | None


# One MITRE mitigation, e.g. M1017 User Training
@dataclass(frozen=True)
class Mitigation:
    mitigation_id: str
    name: str
    description: str
    url: str | None


# "This mitigation helps against this technique"
@dataclass(frozen=True)
class MitigationLink:
    technique_id: str
    mitigation_id: str


def download(cache_path: Path = DEFAULT_CACHE, force: bool = False) -> Path:
    """Download the corpus JSON to cache_path. Skips if file already exists."""
    # Already downloaded? Use the local copy
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and not force:
        return cache_path
    # Otherwise download and save it
    with urllib.request.urlopen(MITRE_URL, timeout=120) as resp:
        data = resp.read()
    cache_path.write_bytes(data)
    return cache_path


def _external_id_and_url(obj: dict) -> tuple[str | None, str | None]:
    # Find the official MITRE id (T1566 / M1017) and its web page
    for ref in obj.get("external_references", []):
        if ref.get("source_name") == "mitre-attack":
            return ref.get("external_id"), ref.get("url")
    return None, None


def parse(cache_path: Path = DEFAULT_CACHE) -> list[Technique]:
    """Parse cached STIX into Technique records (skips revoked/deprecated)."""
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    objects = raw.get("objects", [])

    # Pass 1: map MITRE's internal ids to T-codes (skip retired techniques)
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

    # Map sub-technique -> parent technique
    sub_parent: dict[str, str] = {}
    for o in objects:
        if o.get("type") == "relationship" and o.get("relationship_type") == "subtechnique-of":
            child = o.get("source_ref")
            parent = o.get("target_ref")
            if child and parent:
                sub_parent[child] = parent

    # Pass 2: build a Technique record for every active technique
    out: list[Technique] = []
    for o in objects:
        if o.get("type") != "attack-pattern":
            continue
        if o.get("revoked") or o.get("x_mitre_deprecated"):
            continue
        tcode, url = _external_id_and_url(o)
        if not tcode:
            continue
        # Tactics = which attack phase(s) it belongs to (e.g. initial-access)
        tactics = tuple(
            phase.get("phase_name", "")
            for phase in o.get("kill_chain_phases", [])
            if phase.get("kill_chain_name") == "mitre-attack"
        )
        # For sub-techniques (T1566.001), find the parent's T-code
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


def parse_mitigations(
    cache_path: Path = DEFAULT_CACHE,
) -> tuple[list[Mitigation], list[MitigationLink]]:
    """Parse MITRE mitigations (Mxxxx) and which techniques they mitigate."""
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    objects = raw.get("objects", [])

    # Collect all active mitigations and map their internal ids to M-codes
    # STIX-id -> external code, for both ends of the 'mitigates' relationship.
    coa_stix_to_mid: dict[str, str] = {}
    mitigations: list[Mitigation] = []
    for o in objects:
        if o.get("type") != "course-of-action":
            continue
        if o.get("revoked") or o.get("x_mitre_deprecated"):
            continue
        mid, url = _external_id_and_url(o)
        if not mid:
            continue
        coa_stix_to_mid[o["id"]] = mid
        mitigations.append(
            Mitigation(
                mitigation_id=mid,
                name=o.get("name", ""),
                description=o.get("description", ""),
                url=url,
            )
        )

    # Map technique internal ids to T-codes
    tech_stix_to_tcode: dict[str, str] = {}
    valid_tcodes: set[str] = set()
    for o in objects:
        if o.get("type") != "attack-pattern":
            continue
        if o.get("revoked") or o.get("x_mitre_deprecated"):
            continue
        tcode, _ = _external_id_and_url(o)
        if tcode:
            tech_stix_to_tcode[o["id"]] = tcode
            valid_tcodes.add(tcode)

    # Read the "mitigates" links and keep each (technique, mitigation) pair once
    links: list[MitigationLink] = []
    seen: set[tuple[str, str]] = set()
    for o in objects:
        if o.get("type") != "relationship":
            continue
        if o.get("relationship_type") != "mitigates":
            continue
        if o.get("revoked") or o.get("x_mitre_deprecated"):
            continue
        mid = coa_stix_to_mid.get(o.get("source_ref", ""))
        tcode = tech_stix_to_tcode.get(o.get("target_ref", ""))
        if not mid or not tcode or tcode not in valid_tcodes:
            continue
        key = (tcode, mid)
        if key in seen:
            continue
        seen.add(key)
        links.append(MitigationLink(technique_id=tcode, mitigation_id=mid))

    return mitigations, links
