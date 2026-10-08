"""Synthetic IOC test cases with ground truth BY CONSTRUCTION.

Each case is built by filling a sentence template with known IOC values, so
the expected answer is exact — no human labelling, no ambiguity. Cases mix:
  * plain and defanged forms ([.], (.), hxxp, [at]),
  * every supported IOC type,
  * distractors that LOOK like IOCs but aren't (file names, library names,
    version strings, invalid octets) — these measure precision,
  * Monero (XMR) addresses, which SilkVault posts really use and which the
    extractor does NOT support — these honestly measure a recall gap.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class Case:
    text: str
    expected: set[tuple[str, str]] = field(default_factory=set)   # (type, value)


IPS = ["185.220.101.45", "45.61.184.220", "194.165.16.38", "23.94.215.6", "91.219.236.18"]
DOMAINS = ["secure-update.xyz", "okta-sso.help", "fast-paste.su", "acmecorp-vpn.online",
           "files.example.co.uk"]
CVES = ["CVE-2024-3400", "CVE-2023-46805", "CVE-2024-27198", "CVE-2024-1086"]
SHA256 = ["9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
          "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae"]
MD5 = ["d41d8cd98f00b204e9800998ecf8427e", "098f6bcd4621d373cade4e832627b4f6"]
BTC = ["bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh", "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"]
EMAILS = ["ops@evil-corp.com", "drop@protonmail.com"]
XMR = ["44AFFq5kSiGBoZ4NMDwYtN18obc8AemS33DBLWs3H7otXft3XjrpDtQGv7SqSsaB"]

# Things a naive extractor would wrongly report.
DISTRACTORS = [
    "the loader.exe needs node.js",
    "edit config.yaml then run setup.py",
    "requires python 3.12 and openssl 3.0.13",
    "octets like 999.10.10.10 are not addresses",
    "the readme.md explains it",
]
# A real, known false-positive class we deliberately leave in the set: a
# four-part version string is indistinguishable from an IPv4 by syntax.
VERSION_FP = "patched in build 4.2.1.7"


def _defang(v: str) -> str:
    return v.replace(".", "[.]", 1)


def build_cases(n: int = 80, seed: int = 1337) -> list[Case]:
    rng = random.Random(seed)
    cases: list[Case] = []
    for i in range(n):
        parts: list[str] = []
        exp: set[tuple[str, str]] = set()

        ip = rng.choice(IPS)
        parts.append(f"C2 at {_defang(ip) if rng.random() < 0.5 else ip}")
        exp.add(("ipv4", ip))

        d = rng.choice(DOMAINS)
        parts.append(f"landing on {_defang(d) if rng.random() < 0.5 else d}")
        exp.add(("domain", d))

        if rng.random() < 0.6:
            c = rng.choice(CVES)
            parts.append(f"entry via {c.lower() if rng.random() < 0.3 else c}")
            exp.add(("cve", c))
        if rng.random() < 0.5:
            h = rng.choice(SHA256)
            parts.append(f"sha256 {h.upper() if rng.random() < 0.3 else h}")
            exp.add(("sha256", h))
        if rng.random() < 0.3:
            h = rng.choice(MD5)
            parts.append(f"md5 {h}")
            exp.add(("md5", h))
        if rng.random() < 0.5:
            b = rng.choice(BTC)
            parts.append(f"pay {b}")
            exp.add(("btc", b))
        if rng.random() < 0.3:
            e = rng.choice(EMAILS)
            parts.append(f"contact {e.replace('@', '[at]') if rng.random() < 0.5 else e}")
            exp.add(("email", e))
        if rng.random() < 0.3:
            u = f"https://{rng.choice(DOMAINS)}/drop"
            parts.append(f"get it at {u.replace('https', 'hxxps')}")
            exp.add(("url", u))
        if rng.random() < 0.25:
            x = rng.choice(XMR)
            parts.append(f"or XMR {x}")
            exp.add(("xmr", x))          # unsupported type -> counts as a miss
        if rng.random() < 0.5:
            parts.append(rng.choice(DISTRACTORS))
        if i % 10 == 0:
            parts.append(VERSION_FP)     # expected: nothing

        rng.shuffle(parts)
        cases.append(Case(". ".join(parts) + ".", exp))
    return cases
