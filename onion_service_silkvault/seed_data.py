"""
Seed the SilkVault forum DB with realistic-looking darknet listings + messages.

SilkVault is SentinelX's second synthetic forum. Its seed content is written to
be *recognisably its own forum* — different boards, vendors, and listing styles
than DarkBay — while still being dense with IOCs (IPs, CVEs, BTC wallets,
hashes, domains, actors) so the SentinelX pipeline has signal to extract.

Idempotent-ish: if VAULT_DB already exists with data, this appends a fresh
batch. Pass --reset to drop + recreate from schema.sql.

Usage:
    python seed_data.py            # init (if needed) and seed
    python seed_data.py --reset    # drop tables + reseed from scratch
    python seed_data.py --count 70 # change batch size (default 55)
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent.resolve()
DB_PATH = os.environ.get("VAULT_DB", str(HERE / "vault.db"))
SCHEMA = HERE / "schema.sql"

# SilkVault's boards — distinct names from DarkBay's categories.
BOARDS = ["exploits", "accounts", "network-access", "malware", "data-leaks", "lounge"]

# SilkVault vendor handles — a separate persona pool from DarkBay's USERS.
VENDORS = [
    "silk_quartz", "obsidian_v", "nullcaravan", "graywolf_ops", "pale_horizon",
    "ember_unit", "cipher_orchard", "tin_oracle", "saltminer", "vault_keeper_7",
    "rust_lattice", "hex_marrow", "quiet_signal", "fenix_drop", "umbra_trade",
    "kobold_market", "static_veil", "moss_protocol", "deadlight_co", "iron_petal",
]

# ---- Realistic-but-fake IOC pools ---- #

FAKE_IPS = [
    "91.219.236.18", "45.137.21.9", "194.26.135.72", "5.252.178.41",
    "185.243.115.84", "23.94.215.6", "171.25.193.77", "104.244.76.13",
]
FAKE_DOMAINS = [
    "vault-escrow[.]onion-mirror[.]cc", "pgp-keyserver[.]live",
    "invoice-secure[.]top", "cdn-jquery-min[.]xyz",
    "login-portal-verify[.]click", "fast-paste[.]su",
]
FAKE_BTC = [
    "bc1q9d8h4 jp7r2n6w0qz3v5x8c1m4k7t0y2u5i8o3a",  # spaced on purpose? no — fix below
    "bc1qe7m2p9k4r6t8w1n3x5z7c0v2b4n6m8q0w2e4r",
    "bc1q5t8y2u4i6o8a0s2d4f6g8h0j2k4l6z8x0c2v4",
]
FAKE_MONERO = [
    "48jewbtxe4jU8Gj3vQUq2 oXqZ9rZ6pP — placeholder",  # replaced below
]
FAKE_SHA256 = [
    "a3f5d8e1c2b94706f8a1d2e3c4b5a6978d0e1f2a3b4c5d6e7f8091a2b3c4d5e6",
    "7d4e8a1b2c3f5069a8b7c6d5e4f30219b8a7c6d5e4f3021987a6b5c4d3e2f10a",
    "1f2e3d4c5b6a7980f1e2d3c4b5a69788d7c6b5a49382f1e0d9c8b7a6958473f2",
]
FAKE_CVES = [
    "CVE-2024-23897", "CVE-2024-4577", "CVE-2024-27198", "CVE-2023-34362",
    "CVE-2024-1086", "CVE-2024-21762", "CVE-2023-4966",
]
ACTORS = [
    "APT41", "Sandworm", "Kimsuky", "TA505", "Akira", "Play", "Medusa",
    "RansomHub", "Storm-0501", "Mustang Panda",
]
MALWARE = [
    "AsyncRAT", "Remcos", "DarkGate", "Lumma Stealer", "SystemBC",
    "Pikabot", "Latrodectus", "NetSupport RAT", "Amadey",
]
SECTORS = [
    "Canadian energy utility", "German automotive supplier",
    "Singapore payment processor", "UK NHS trust",
    "Texan school district", "Nordic shipping line",
    "Gulf-region oil services firm", "Mexican credit union",
]

# Wallet pools above had readability placeholders; normalise to clean strings
# here so the seed never emits malformed addresses.
FAKE_BTC = [
    "bc1q9d8h4jp7r2n6w0qz3v5x8c1m4k7t0y2u5i8o3a",
    "bc1qe7m2p9k4r6t8w1n3x5z7c0v2b4n6m8q0w2e4r5",
    "bc1q5t8y2u4i6o8a0s2d4f6g8h0j2k4l6z8x0c2v4b",
]
FAKE_MONERO = [
    "48jewbtxe4jU8Gj3vQUq2oXqZ9rZ6pPtH4mN7kL2sV9wA1bC3dE5fG7hJ9kL1mN3",
    "44AFFq5kSiGBoZ4NMDwYtN18obc8AemS33DBLWs3H7otXft3XjrpDtQGv7SqSsaB",
]


# Each template renders to a believable SilkVault listing. They mix boards,
# IOCs, and prose. SilkVault leans more "vendor catalogue / escrow" in tone
# than DarkBay's bbs chatter — another way the two forums read differently.
TEMPLATES: list[tuple[str, str, str]] = [
    # (board, title_template, body_template)
    (
        "network-access",
        "VENDOR LISTING :: corp network foothold -- {sector}",
        "verified foothold inside a {sector}. entry via {cve} on their edge "
        "appliance. current dwell ~{days}d, {hosts} hosts visible, no EDR on "
        "the segment i landed in. C2 staged at {ip}:8443. escrow only, "
        "asking {price} XMR -> {xmr}. proof pack on request after escrow funds.",
    ),
    (
        "accounts",
        "[STOCK] {service} account bundle -- {n}k units",
        "restocked: {n}k {service} accounts, cookie+token format so MFA is "
        "already satisfied. checker output {pct}% live as of {date}. "
        "sample line on request. BTC escrow {btc}. bulk discount over 5k.",
    ),
    (
        "exploits",
        "WEAPONIZED :: {cve} exploit chain w/ loader",
        "selling a stable weaponized chain for {cve}. unauth -> SYSTEM in one "
        "shot, ships with a {malware} loader stub. pops a patched lab build. "
        "exploit archive sha256 {sha}. {price} XMR, escrow via vault mod. "
        "no resale, watermarked builds.",
    ),
    (
        "malware",
        "{malware} builder -- private, FUD {date}",
        "private {malware} builder, not the leaked cracked one. crypter "
        "included, FUD across mainstream engines as of {date}. infra rotates, "
        "current panel reachable through {domain}. {price} XMR -> {xmr}. "
        "30d support, escrow enforced.",
    ),
    (
        "data-leaks",
        "DB DUMP :: {sector} -- customer + internal",
        "fresh dump from a {sector}. customer PII plus internal mailflow. "
        "{n}M rows total. exfil routed through {ip}, untouched since pull. "
        "mirror staged at {domain}/pkg sha256 {sha}. escrow {btc}.",
    ),
    (
        "network-access",
        "WTS :: VPN + RDP into {sector}",
        "working VPN and an RDP jump host into a {sector}. their gateway is "
        "at {ip}, creds confirmed this week. local admin on the RDP box. "
        "{actor}-tier buyers preferred, no testers. {price} XMR. escrow.",
    ),
    (
        "exploits",
        "{cve} -- mass-exploitation live",
        "PSA for the board: {cve} is being sprayed in the wild right now. "
        "scanning bursts traced to {ip} since {date}. {actor} looks to be "
        "the crew weaponizing it first. patch or get popped.",
    ),
    (
        "data-leaks",
        "[FREEBIE] {service} partial dump -- goodwill",
        "dropping a partial {service} dump as a goodwill post, ~{n}0k lines. "
        "this is aged ({date}), do not ask for fresh. grab at "
        "{domain}/free.tar sha256 {sha}.",
    ),
    (
        "malware",
        "phishlet :: {service} clone w/ token capture",
        "selling an up-to-date {service} phishlet. landing staged at {domain}, "
        "evilginx-style, lifts the session token so MFA is bypassed. "
        "{price} XMR escrow -> {xmr}.",
    ),
    (
        "lounge",
        "anyone tracking {actor} infra moves?",
        "noticing {actor} rotated C2 again, new beacons out of {ip}. payload "
        "shifted to {malware} from what i can tell. timing matches the {cve} "
        "disclosure. anyone else seeing it on their sinkholes?",
    ),
    (
        "accounts",
        "[WTB] enterprise SSO accounts -- {sector}",
        "buying enterprise SSO / Okta-tier accounts scoped to a {sector}. "
        "cookie format preferred. paying up to {price} XMR by privilege. "
        "escrow only, vouched vendors to the front.",
    ),
]

REPLY_TEMPLATES = [
    "vouch — escrow cleared with this vendor twice, delivery as described.",
    "scam flag: same {service} dump was floating on another board in {date}.",
    "what's the exact entry vector — {cve} again or something newer?",
    "funding escrow now, send the proof pack.",
    "can a mod confirm this vendor's watermark? builds get resold.",
    "saw that C2 IP {ip} in my telemetry yesterday — checks out.",
    "is the {malware} stub detected by anything current?",
    "{actor} affiliated or independent? matters for my buyers.",
    "taking a unit — XMR sub-address: {xmr}",
    "moved to escrow, vault mod is holding.",
]


def init_schema(conn: sqlite3.Connection) -> None:
    with open(SCHEMA, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()


def reset_db(conn: sqlite3.Connection) -> None:
    conn.executescript("DROP TABLE IF EXISTS messages; DROP TABLE IF EXISTS listings;")
    conn.commit()
    init_schema(conn)


def _fields() -> dict[str, object]:
    return {
        "sector": random.choice(SECTORS),
        "hosts": random.choice([60, 180, 700, 2400]),
        "days": random.randint(3, 28),
        "price": random.choice([2, 4, 6, 9, 14, 22]),
        "btc": random.choice(FAKE_BTC),
        "xmr": random.choice(FAKE_MONERO),
        "n": random.choice([1, 2, 4, 8, 15, 30]),
        "service": random.choice(
            ["Microsoft 365", "Google Workspace", "Coinbase", "PayPal",
             "Citrix", "Salesforce", "AWS"]
        ),
        "pct": random.choice([58, 67, 74, 81, 88]),
        "cve": random.choice(FAKE_CVES),
        "ip": random.choice(FAKE_IPS),
        "sha": random.choice(FAKE_SHA256),
        "malware": random.choice(MALWARE),
        "actor": random.choice(ACTORS),
        "date": (
            datetime.now(timezone.utc) - timedelta(days=random.randint(1, 60))
        ).strftime("%Y-%m"),
        "domain": random.choice(FAKE_DOMAINS),
    }


def random_listing_body(board: str) -> tuple[str, str]:
    """Return (title, body) drawing from templates whose board matches."""
    candidates = [t for t in TEMPLATES if t[0] == board]
    if not candidates:
        candidates = TEMPLATES
    _, title_tpl, body_tpl = random.choice(candidates)
    f = _fields()
    return title_tpl.format(**f), body_tpl.format(**f)


def random_reply_body() -> str:
    return random.choice(REPLY_TEMPLATES).format(**_fields())


def seed(conn: sqlite3.Connection, n_listings: int) -> None:
    """Insert n_listings listings, each with 1–6 messages (incl. the opener)."""
    now = time.time()
    listings_inserted = 0
    messages_inserted = 0

    for _ in range(n_listings):
        board = random.choice(BOARDS)
        vendor = random.choice(VENDORS)
        # Spread listing creation over the last 14 days.
        listing_ts = now - random.uniform(0, 14 * 86400)
        title, opener_body = random_listing_body(board)

        cur = conn.execute(
            "INSERT INTO listings (title, board, vendor, created_at) "
            "VALUES (?, ?, ?, ?)",
            (title, board, vendor, listing_ts),
        )
        listing_id = cur.lastrowid
        listings_inserted += 1

        # Opening message — authored by the vendor.
        conn.execute(
            "INSERT INTO messages (listing_id, author, body, created_at) "
            "VALUES (?, ?, ?, ?)",
            (listing_id, vendor, opener_body, listing_ts),
        )
        messages_inserted += 1

        # 0–5 replies, each strictly after the opener.
        last_ts = listing_ts
        for _ in range(random.randint(0, 5)):
            last_ts += random.uniform(60, 4 * 3600)
            if last_ts > now:
                break
            conn.execute(
                "INSERT INTO messages (listing_id, author, body, created_at) "
                "VALUES (?, ?, ?, ?)",
                (listing_id, random.choice(VENDORS), random_reply_body(), last_ts),
            )
            messages_inserted += 1

    conn.commit()
    print(f"seeded {listings_inserted} listings / {messages_inserted} messages "
          f"-> {DB_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="drop + recreate tables")
    parser.add_argument("--count", type=int, default=55,
                        help="how many listings to seed")
    parser.add_argument("--seed", type=int, default=None,
                        help="RNG seed for reproducibility")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        if args.reset:
            reset_db(conn)
        else:
            init_schema(conn)
        seed(conn, args.count)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
