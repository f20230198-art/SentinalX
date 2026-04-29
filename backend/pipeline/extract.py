"""IOC + named-entity extraction over scraped post bodies.

IOC types:
    ipv4, ipv6, cve, md5, sha1, sha256, btc, url, domain, email

Defanging:
    Threat-intel writeups commonly defang IOCs so they can't be clicked or
    auto-resolved. We refang into a working copy before regex matching:
        1.2.3[.]4   ->  1.2.3.4
        1.2.3(.)4   ->  1.2.3.4
        hxxp://     ->  http://
        hxxps://    ->  https://
        evil[.]com  ->  evil.com
    Spans returned are over the *refanged* string. Storing both the original
    and refanged spans is overkill for our purposes; the value itself is what
    downstream stages care about.

Entities:
    spaCy en_core_web_sm for ORG / PERSON / PRODUCT / GPE / NORP / EVENT.
    A small curated keyword pass for MALWARE and THREAT_ACTOR — spaCy out of
    the box does not know what "Cobalt Strike" or "Lazarus Group" is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

import spacy

# --- regex patterns --------------------------------------------------------- #

# IPv4: four octets 0-255. We use a permissive pattern then validate octets.
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# IPv6: simplified — 2+ colons, hex groups. Good enough for forum text.
_IPV6_RE = re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}\b")

_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)

_MD5_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
_SHA1_RE = re.compile(r"\b[a-fA-F0-9]{40}\b")
_SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")

# Bitcoin: legacy P2PKH/P2SH (1.., 3..) and bech32 (bc1..). Not exhaustive but
# covers the formats most darknet markets advertise.
_BTC_RE = re.compile(r"\b(?:bc1[a-z0-9]{25,62}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")

_URL_RE = re.compile(r"\bhttps?://[^\s<>\"'\)]+", re.IGNORECASE)

# Email: pragmatic, not RFC 5322 perfect.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Domain: hostname with at least one dot and a 2-24 char TLD. Run last and
# subtract anything already matched as URL/email to avoid double-counting.
_DOMAIN_RE = re.compile(
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[A-Za-z]{2,24}\b"
)

# Curated keyword lists. Tiny on purpose — Stage 5 (MITRE + vector index) is
# where real coverage comes from. These are just enough to demonstrate the
# pattern and to give the seed data something to hit.
_MALWARE_TERMS = {
    "Cobalt Strike", "Mimikatz", "Emotet", "TrickBot", "Ryuk", "Conti",
    "LockBit", "BlackCat", "ALPHV", "REvil", "Sodinokibi", "Maze",
    "QakBot", "IcedID", "BumbleBee", "Raspberry Robin", "Pikabot",
}
_THREAT_ACTOR_TERMS = {
    "Lazarus Group", "APT28", "APT29", "APT41", "FIN7", "FIN11",
    "Sandworm", "Cozy Bear", "Fancy Bear", "Wizard Spider",
    "Scattered Spider", "Lapsus$",
}


@dataclass(frozen=True)
class Match:
    type: str       # for IOCs: ioc_type; for entities: spaCy/custom label
    value: str
    span: tuple[int, int]


# --- defanging -------------------------------------------------------------- #

_DEFANG_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\[\.\]"), "."),
    (re.compile(r"\(\.\)"), "."),
    (re.compile(r"\{\.\}"), "."),
    (re.compile(r"\[at\]", re.IGNORECASE), "@"),
    (re.compile(r"\(at\)", re.IGNORECASE), "@"),
    (re.compile(r"\bhxxps?://", re.IGNORECASE), lambda m: m.group(0).replace("hxxp", "http").replace("HXXP", "HTTP")),
]


def refang(text: str) -> str:
    out = text
    for pat, repl in _DEFANG_PATTERNS:
        out = pat.sub(repl, out)
    return out


# --- IOC extractor ---------------------------------------------------------- #

def _valid_ipv4(s: str) -> bool:
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


class IOCExtractor:
    def extract(self, text: str) -> list[Match]:
        t = refang(text)
        out: list[Match] = []
        spans_consumed: list[tuple[int, int]] = []

        def add(ioc_type: str, m: re.Match, value: str | None = None) -> None:
            v = value if value is not None else m.group(0)
            out.append(Match(ioc_type, v, m.span()))
            spans_consumed.append(m.span())

        for m in _URL_RE.finditer(t):
            add("url", m)

        for m in _EMAIL_RE.finditer(t):
            add("email", m)

        for m in _IPV4_RE.finditer(t):
            if _valid_ipv4(m.group(0)):
                add("ipv4", m)

        for m in _IPV6_RE.finditer(t):
            # filter the tiniest false positives like "1:2"
            if m.group(0).count(":") >= 2:
                add("ipv6", m)

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

        for m in _BTC_RE.finditer(t):
            add("btc", m)

        # Domains last, skip anything already inside a URL or email.
        for m in _DOMAIN_RE.finditer(t):
            if _overlaps(m.span(), spans_consumed):
                continue
            add("domain", m, value=m.group(0).lower())

        return out


def _overlaps(span: tuple[int, int], consumed: list[tuple[int, int]]) -> bool:
    s, e = span
    for cs, ce in consumed:
        if s < ce and cs < e:
            return True
    return False


# --- Entity extractor ------------------------------------------------------- #

# spaCy labels we keep. PERSON/ORG/GPE/NORP/PRODUCT/EVENT are the ones with
# CTI signal; the rest (DATE, CARDINAL, ORDINAL, ...) are noise here.
_KEEP_LABELS = {"PERSON", "ORG", "GPE", "NORP", "PRODUCT", "EVENT", "LOC"}


class EntityExtractor:
    def __init__(self, model: str = "en_core_web_sm") -> None:
        # Disable the parser — we only need the NER component, and the parser
        # is the slowest pipe in the small model.
        self.nlp = spacy.load(model, disable=["parser", "lemmatizer"])

    def extract(self, text: str) -> list[Match]:
        doc = self.nlp(text)
        out: list[Match] = []
        for ent in doc.ents:
            if ent.label_ in _KEEP_LABELS:
                out.append(Match(ent.label_, ent.text, (ent.start_char, ent.end_char)))

        # Curated keyword pass. Case-sensitive on purpose — "Conti" the
        # ransomware vs "conti" the substring is a real disambiguation.
        for term in _MALWARE_TERMS:
            for m in re.finditer(rf"\b{re.escape(term)}\b", text):
                out.append(Match("MALWARE", term, m.span()))
        for term in _THREAT_ACTOR_TERMS:
            for m in re.finditer(rf"\b{re.escape(term)}\b", text):
                out.append(Match("THREAT_ACTOR", term, m.span()))

        return out


# --- combined helper -------------------------------------------------------- #

def extract_all(
    text: str,
    ioc: IOCExtractor,
    ent: EntityExtractor,
) -> tuple[list[Match], list[Match]]:
    return ioc.extract(text), ent.extract(text)


def dedupe(matches: Iterable[Match]) -> list[Match]:
    """Collapse (type, value) duplicates within a single post.

    We keep the first span we saw for each (type, value). The DB has the same
    UNIQUE invariant, but pre-collapsing avoids one IntegrityError per dup.
    """
    seen: dict[tuple[str, str], Match] = {}
    for m in matches:
        key = (m.type, m.value)
        if key not in seen:
            seen[key] = m
    return list(seen.values())
