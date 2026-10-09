"""Extract IOCs (IPs, hashes, CVEs, BTC, URLs, domains, emails) and named entities from posts.
Defanged IOCs like 1.2.3[.]4 or hxxp:// are fixed before matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

import spacy
import tldextract

# Offline public-suffix list, to check if a domain is real
_TLD = tldextract.TLDExtract(suffix_list_urls=())


def _is_real_domain(candidate: str) -> bool:
    """True only if it ends in a real suffix (so node.js / file.exe aren't domains)."""
    ext = _TLD(candidate)
    return bool(ext.suffix) and bool(ext.domain)


# --- regex patterns --------------------------------------------------------- #

# IPv4: four octets 0-255. We use a permissive pattern then validate octets.
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# IPv6: simplified — 2+ colons, hex groups. Good enough for forum text.
_IPV6_RE = re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}\b")

# CVE ids like CVE-2024-12345
_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)

# File hashes (by length: 32 = MD5, 40 = SHA1, 64 = SHA256)
_MD5_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
_SHA1_RE = re.compile(r"\b[a-fA-F0-9]{40}\b")
_SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")

# Bitcoin addresses (legacy 1.../3... and bech32 bc1...)
_BTC_RE = re.compile(r"\b(?:bc1[a-z0-9]{25,62}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")

# http(s) links
_URL_RE = re.compile(r"\bhttps?://[^\s<>\"'\)]+", re.IGNORECASE)

# Email: pragmatic, not RFC 5322 perfect.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Domains (matched last; skip ones already found as URL/email)
_DOMAIN_RE = re.compile(
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[A-Za-z]{2,24}\b"
)

# Small hand-picked malware / threat-actor names spaCy doesn't know
_MALWARE_TERMS = {
    "Cobalt Strike", "Mimikatz", "Emotet", "TrickBot", "Ryuk", "Conti",
    "LockBit", "BlackCat", "ALPHV", "REvil", "Sodinokibi", "Maze",
    "QakBot", "IcedID", "BumbleBee", "Raspberry Robin", "Pikabot",
}
# Known threat-actor group names
_THREAT_ACTOR_TERMS = {
    "Lazarus Group", "APT28", "APT29", "APT41", "FIN7", "FIN11",
    "Sandworm", "Cozy Bear", "Fancy Bear", "Wizard Spider",
    "Scattered Spider", "Lapsus$",
}


# One thing we found: its type, the text, and where it is in the post
@dataclass(frozen=True)
class Match:
    type: str       # for IOCs: ioc_type; for entities: spaCy/custom label
    value: str
    span: tuple[int, int]


# --- defanging -------------------------------------------------------------- #

# Patterns that undo "defanging": [.] -> . , [at] -> @ , hxxp -> http
_DEFANG_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\[\.\]"), "."),
    (re.compile(r"\(\.\)"), "."),
    (re.compile(r"\{\.\}"), "."),
    (re.compile(r"\[at\]", re.IGNORECASE), "@"),
    (re.compile(r"\(at\)", re.IGNORECASE), "@"),
    (re.compile(r"\bhxxps?://", re.IGNORECASE), lambda m: m.group(0).replace("hxxp", "http").replace("HXXP", "HTTP")),
]


def refang(text: str) -> str:
    # Apply every fix-up pattern to the text
    out = text
    for pat, repl in _DEFANG_PATTERNS:
        out = pat.sub(repl, out)
    return out


# --- IOC extractor ---------------------------------------------------------- #

def _valid_ipv4(s: str) -> bool:
    # Check each of the 4 parts is a number 0-255
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


class IOCExtractor:
    # Run all IOC patterns over the text; returns every match found
    def extract(self, text: str) -> list[Match]:
        # Fix defanged IOCs first
        t = refang(text)
        out: list[Match] = []
        # Remember which parts of the text are already used by a match
        spans_consumed: list[tuple[int, int]] = []

        # Helper: record a match and mark its text as used
        def add(ioc_type: str, m: re.Match, value: str | None = None) -> None:
            v = value if value is not None else m.group(0)
            out.append(Match(ioc_type, v, m.span()))
            spans_consumed.append(m.span())

        for m in _URL_RE.finditer(t):
            # Drop trailing punctuation from URLs
            add("url", m, value=m.group(0).rstrip(".,;:!?"))

        # Emails
        for m in _EMAIL_RE.finditer(t):
            add("email", m)

        # IPv4 (only real ones, e.g. 999.1.1.1 is rejected)
        for m in _IPV4_RE.finditer(t):
            if _valid_ipv4(m.group(0)):
                add("ipv4", m)

        for m in _IPV6_RE.finditer(t):
            # filter the tiniest false positives like "1:2"
            if m.group(0).count(":") >= 2:
                add("ipv6", m)

        # CVE ids, always upper-case
        for m in _CVE_RE.finditer(t):
            add("cve", m, value=m.group(0).upper())

        # Hash extraction: longest first, so a 64-hex string is sha256 not three md5s.
        for m in _SHA256_RE.finditer(t):
            add("sha256", m, value=m.group(0).lower())
        for m in _SHA1_RE.finditer(t):
            if not _overlaps(m.span(), spans_consumed):
                add("sha1", m, value=m.group(0).lower())
        for m in _MD5_RE.finditer(t):
            if not _overlaps(m.span(), spans_consumed):
                add("md5", m, value=m.group(0).lower())

        # Bitcoin wallets
        for m in _BTC_RE.finditer(t):
            add("btc", m)

        # Domains last, skip anything already inside a URL or email.
        for m in _DOMAIN_RE.finditer(t):
            if _overlaps(m.span(), spans_consumed):
                continue
            if not _is_real_domain(m.group(0)):
                continue
            add("domain", m, value=m.group(0).lower())

        return out


def _overlaps(span: tuple[int, int], consumed: list[tuple[int, int]]) -> bool:
    # True if this span overlaps any span already used
    s, e = span
    for cs, ce in consumed:
        if s < ce and cs < e:
            return True
    return False


# --- Entity extractor ------------------------------------------------------- #

# spaCy entity types worth keeping
_KEEP_LABELS = {"PERSON", "ORG", "GPE", "NORP", "PRODUCT", "EVENT", "LOC"}


class EntityExtractor:
    # Finds names (people, orgs, places, products) with spaCy + our keyword lists
    def __init__(self, model: str = "en_core_web_sm") -> None:
        # Only NER is needed; skip the slow parts
        self.nlp = spacy.load(model, disable=["parser", "lemmatizer"])

    def extract(self, text: str) -> list[Match]:
        # spaCy's named entities, keeping only useful types
        doc = self.nlp(text)
        out: list[Match] = []
        for ent in doc.ents:
            if ent.label_ in _KEEP_LABELS:
                out.append(Match(ent.label_, ent.text, (ent.start_char, ent.end_char)))

        # Case-sensitive on purpose ("Conti" vs "conti")
        for term in _MALWARE_TERMS:
            for m in re.finditer(rf"\b{re.escape(term)}\b", text):
                out.append(Match("MALWARE", term, m.span()))
        for term in _THREAT_ACTOR_TERMS:
            for m in re.finditer(rf"\b{re.escape(term)}\b", text):
                out.append(Match("THREAT_ACTOR", term, m.span()))

        return out


# --- combined helper -------------------------------------------------------- #

# Run both extractors on the same text
def extract_all(
    text: str,
    ioc: IOCExtractor,
    ent: EntityExtractor,
) -> tuple[list[Match], list[Match]]:
    return ioc.extract(text), ent.extract(text)


def dedupe(matches: Iterable[Match]) -> list[Match]:
    """Remove duplicate (type, value) matches within one post."""
    seen: dict[tuple[str, str], Match] = {}
    for m in matches:
        # Keep only the first match for each (type, value)
        key = (m.type, m.value)
        if key not in seen:
            seen[key] = m
    return list(seen.values())
