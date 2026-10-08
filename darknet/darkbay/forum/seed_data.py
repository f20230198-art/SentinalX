"""
Seed the synthetic forum DB with realistic-looking darknet threads + posts.

Idempotent-ish: if FORUM_DB already exists with data, this will *append* a fresh
batch. Pass --reset to drop + recreate from schema.sql.

The posts are intentionally rich in IOCs (IPs, CVEs, BTC addresses, hashes,
domains, threat actor names) so the downstream NER + LLM pipeline has something
to extract.

Usage:
    python seed_data.py            # init (if needed) and seed
    python seed_data.py --reset    # drop tables + reseed from scratch
    python seed_data.py --count 80 # change batch size (default 50)
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
DB_PATH = os.environ.get("FORUM_DB", str(HERE / "forum.db"))
SCHEMA = HERE / "schema.sql"

CATEGORIES = ["marketplace", "credentials", "access", "vulnerabilities", "general"]

USERS = [
    "shadowbroker", "n3cr0", "voidwalker", "0xdeadbeef", "ghostpack", "bl4ck0ut",
    "kr4ken", "synth1cide", "midn1ght", "ph4ntom", "scriptkid_99", "lazarus_fan",
    "redteam42", "fancy_bear_lite", "cobaltkiddo", "ransom_dev", "creds4cash",
    "rdp_lord", "0day_addict", "darknet_dan",
]

# ---- Realistic-but-fake IOC pools ---- #

FAKE_IPS = [
    "185.220.101.45", "45.61.184.220", "194.165.16.38", "5.181.86.214",
    "192.42.116.16", "23.129.64.218", "176.123.1.233", "162.247.74.27",
]
FAKE_DOMAINS = [
    "secure-update[.]xyz", "outlook-verify[.]top", "ms-teams-cdn[.]click",
    "docusign-portal[.]live", "okta-sso[.]help", "acmecorp-vpn[.]online",
]
FAKE_BTC = [
    "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
    "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
    "bc1q34aq5drpuwy3wgl9lhup9892qp6svr8ldzyy7c",
]
FAKE_SHA256 = [
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",
]
FAKE_CVES = [
    "CVE-2024-1709", "CVE-2024-21413", "CVE-2023-46805", "CVE-2024-3400",
    "CVE-2023-22515", "CVE-2024-21887", "CVE-2024-30040",
]
ACTORS = [
    "APT28", "APT29", "Lazarus", "FIN7", "Conti", "LockBit", "BlackCat",
    "Cl0p", "Scattered Spider", "Volt Typhoon",
]
MALWARE = [
    "Emotet", "TrickBot", "Cobalt Strike", "Brute Ratel", "RedLine",
    "Vidar", "Raccoon Stealer", "Qakbot", "IcedID",
]
INDUSTRIES = [
    "US healthcare network", "EU manufacturing firm", "Indian fintech",
    "Brazilian retailer", "Japanese auto OEM", "Australian telco",
    "Fortune 500 logistics company", "regional municipal government",
]


# Each template renders into a believable post body. They mix categories,
# IOCs, and prose so NER + LLM extraction has signal to chew on.
TEMPLATES: list[tuple[str, str, str]] = [
    # (category, title, body_template)
    (
        "access",
        "[SELLING] RDP access -- {industry}, domain admin",
        "got fresh DA on a {industry}. ~{users} users, ~{hosts} hosts. tested "
        "stable for {days} days. price: 0.{price} BTC -> {btc}. ping me on "
        "session for proof. exfil already done so price is firm.",
    ),
    (
        "credentials",
        "[FRESH] {n}M {service} combos -- valid {pct}%",
        "dump from {service} breach. {n}M lines email:pass. private check ran "
        "{pct}% valid against the live login. sample: alice@example.com:Hunter2! "
        "/ bob@example.com:Welcome2024. payment in btc only -> {btc}. no refunds.",
    ),
    (
        "vulnerabilities",
        "PoC: {cve} unauth RCE in {product}",
        "wrote a working PoC for {cve}. unauth, pre-auth, no creds needed. "
        "drops a beacon to {ip} on port 4444. tested against patched build "
        "from last month -- still pops. attaching sha256 of the exploit zip: "
        "{sha}. dm if you want it, taking 0.{price} BTC.",
    ),
    (
        "marketplace",
        "[SERVICE] {malware} loader -- FUD as of {date}",
        "selling crypted {malware} loader. AV scan attached, FUD against all "
        "major engines as of {date}. C2 infra rotated weekly. starter pack: "
        "0.{price} BTC. comes with builder + 30 day support. payment to {btc}. "
        "no scam, escrow available via the usual mod.",
    ),
    (
        "access",
        "WTS: VPN creds -- {industry}",
        "I have working VPN credentials for {industry}. confirmed access via "
        "their {ip} gateway. SSO is okta -- okta-sso[.]help phishlet still "
        "valid. asking 0.{price} BTC. {actor} types preferred, no script kids.",
    ),
    (
        "vulnerabilities",
        "{cve} in the wild now",
        "heads up -- {cve} is being mass-scanned. saw exploitation attempts "
        "from {ip} hitting honeypots since yesterday. patch your stuff. "
        "{actor} appears to be the one weaponizing it, based on TTPs.",
    ),
    (
        "credentials",
        "[FREE LEAK] {service} -- 50k lines",
        "dumping 50k {service} accounts as goodwill. don't ask for fresh, "
        "this is from {date}. mirror: {domain}/dump.zip sha256 {sha}. enjoy.",
    ),
    (
        "marketplace",
        "Phishing kit -- {service} clone, {date}",
        "selling latest {service} phishlet. landing page hosted at "
        "{domain}. evilginx2 compatible. bypasses MFA via session token "
        "capture. price 0.{price} BTC -> {btc}.",
    ),
    (
        "general",
        "anyone else seeing {actor} traffic spike?",
        "noticed a bunch of new C2 beacons matching {actor} TTPs hitting my "
        "sinkhole. mostly from {ip}. seems like they switched malware to "
        "{malware}. could just be coincidence but the timing lines up with "
        "the {cve} drop.",
    ),
    (
        "access",
        "[WTB] initial access to any {industry}",
        "buying initial access. webshell, RDP, VPN, doesn't matter as long as "
        "it lands in a {industry}. paying up to 0.{price} BTC depending on "
        "privilege level. escrow only.",
    ),
]

# Non-English threads. Real darknet CTI is heavily multilingual — Russian and
# Spanish forums carry some of the earliest signal. These templates exercise
# the Stage-7 pipeline: language detection + offline translation before
# extraction/LLM. IOC placeholders are kept verbatim (IPs, CVEs, BTC, hashes
# are language-agnostic and IOC regexes run on the *original* body), so a
# translated post still yields the same indicators as an English one.
#
# Tuple shape: (lang, category, title_template, body_template).
MULTILINGUAL_TEMPLATES: list[tuple[str, str, str, str]] = [
    (
        "ru",
        "access",
        "[ПРОДАЮ] RDP доступ -- {industry}, права домен-админа",
        "Есть свежий доступ уровня домен-админ к сети ({industry}). Около "
        "{users} пользователей, {hosts} хостов. Стабильно держится {days} "
        "дней. Цена: 0.{price} BTC на кошелёк {btc}. Шлюз подтверждён через "
        "{ip}. Развёрнут {malware} для постэксплуатации. Пишите в ЛС для "
        "пруфов, цена окончательная.",
    ),
    (
        "ru",
        "vulnerabilities",
        "{cve} -- рабочий эксплойт, неаутентифицированный RCE",
        "Написал рабочий PoC под {cve} в продукте {product}. Без "
        "аутентификации, креды не нужны. Сбрасывает маяк на {ip}, порт 4444. "
        "Протестировано на пропатченной сборке -- всё ещё пробивает. SHA256 "
        "архива с эксплойтом: {sha}. Группировка {actor} уже использует это "
        "в дикой природе. Отдаю за 0.{price} BTC.",
    ),
    (
        "es",
        "credentials",
        "[FRESCO] {n}M combos de {service} -- {pct}% válidos",
        "Volcado de la brecha de {service}. {n}M de líneas correo:clave. "
        "Verificación privada dio {pct}% válido contra el login en vivo. "
        "Muestra: alice@example.com:Hunter2! El espejo está en {domain} y el "
        "SHA256 del archivo es {sha}. Solo pago en BTC a {btc}. Sin reembolsos.",
    ),
    (
        "es",
        "access",
        "[VENDO] credenciales VPN -- {industry}",
        "Tengo credenciales VPN funcionando para una {industry}. Acceso "
        "confirmado por su gateway en {ip}. El SSO es Okta y el phishlet de "
        "okta-sso[.]help sigue activo. La infraestructura C2 usa {malware}. "
        "Pido 0.{price} BTC. Prefiero compradores tipo {actor}, nada de "
        "script kiddies.",
    ),
]


REPLY_TEMPLATES = [
    "vouch, dealt with op last month, legit.",
    "scam. pulled this exact dump from a leak in {date}.",
    "what's the entry vector? {cve} again?",
    "interested. sending session id.",
    "anyone got a working sample? dm me.",
    "saw the same C2 IP {ip} hit my logs yesterday. confirmed.",
    "old news, this was on the other forum two weeks ago.",
    "is this {actor} affiliated or freelance?",
    "i'll take it. wallet: {btc}",
    "moving this to escrow. mod approved.",
]


def init_schema(conn: sqlite3.Connection) -> None:
    with open(SCHEMA, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()


def reset_db(conn: sqlite3.Connection) -> None:
    conn.executescript("DROP TABLE IF EXISTS posts; DROP TABLE IF EXISTS threads;")
    conn.commit()
    init_schema(conn)


def random_post_body(category: str, base_ts: float) -> tuple[str, str]:
    """Return (title, body) drawing from templates whose category matches."""
    candidates = [t for t in TEMPLATES if t[0] == category]
    if not candidates:
        candidates = TEMPLATES
    _, title_tpl, body_tpl = random.choice(candidates)

    fields = _random_fields()
    return title_tpl.format(**fields), body_tpl.format(**fields)


def _random_fields() -> dict[str, object]:
    """The shared placeholder pool used by every post template."""
    return {
        "industry": random.choice(INDUSTRIES),
        "users": random.choice([400, 1200, 3500, 8000, 15000]),
        "hosts": random.choice([80, 250, 900, 3000]),
        "days": random.randint(2, 30),
        "price": random.randint(15, 89),
        "btc": random.choice(FAKE_BTC),
        "n": random.choice([1, 2, 5, 10, 25]),
        "service": random.choice(
            ["Netflix", "Spotify", "LinkedIn", "Adobe", "Okta", "Zoom", "Slack"]
        ),
        "pct": random.choice([62, 71, 78, 84, 91]),
        "cve": random.choice(FAKE_CVES),
        "product": random.choice(
            ["ConnectWise ScreenConnect", "Ivanti Connect Secure",
             "Palo Alto GlobalProtect", "Confluence Data Center",
             "MS Outlook", "MOVEit Transfer"]
        ),
        "ip": random.choice(FAKE_IPS),
        "sha": random.choice(FAKE_SHA256),
        "malware": random.choice(MALWARE),
        "actor": random.choice(ACTORS),
        "date": (
            datetime.now(timezone.utc) - timedelta(days=random.randint(1, 60))
        ).strftime("%Y-%m"),
        "domain": random.choice(FAKE_DOMAINS),
    }


def random_multilingual_post() -> tuple[str, str, str]:
    """Render a random non-English thread.

    Returns (category, title, body) drawn from MULTILINGUAL_TEMPLATES — used to
    salt the corpus with Russian/Spanish posts so the Stage-7 detect+translate
    path has real data to exercise.
    """
    lang, category, title_tpl, body_tpl = random.choice(MULTILINGUAL_TEMPLATES)
    fields = _random_fields()
    return category, title_tpl.format(**fields), body_tpl.format(**fields)


def random_reply_body() -> str:
    fields = {
        "date": (
            datetime.now(timezone.utc) - timedelta(days=random.randint(5, 90))
        ).strftime("%Y-%m"),
        "cve": random.choice(FAKE_CVES),
        "ip": random.choice(FAKE_IPS),
        "actor": random.choice(ACTORS),
        "btc": random.choice(FAKE_BTC),
    }
    return random.choice(REPLY_TEMPLATES).format(**fields)


def seed(conn: sqlite3.Connection, n_threads: int) -> None:
    """Insert n_threads threads, each with 1–6 posts (incl. OP)."""
    now = time.time()
    threads_inserted = 0
    posts_inserted = 0

    multilingual_count = 0
    for i in range(n_threads):
        author = random.choice(USERS)
        # Spread thread creation over last 14 days.
        thread_ts = now - random.uniform(0, 14 * 86400)

        # ~20% of threads are non-English, to exercise the Stage-7
        # detect + translate pipeline. The rest use the English templates.
        if random.random() < 0.20:
            category, title, op_body = random_multilingual_post()
            multilingual_count += 1
        else:
            category = random.choice(CATEGORIES)
            title, op_body = random_post_body(category, thread_ts)

        cur = conn.execute(
            "INSERT INTO threads (title, category, author, created_at) VALUES (?, ?, ?, ?)",
            (title, category, author, thread_ts),
        )
        thread_id = cur.lastrowid
        threads_inserted += 1

        # OP post.
        conn.execute(
            "INSERT INTO posts (thread_id, author, body, created_at) VALUES (?, ?, ?, ?)",
            (thread_id, author, op_body, thread_ts),
        )
        posts_inserted += 1

        # 0–5 replies, each strictly after the OP.
        n_replies = random.randint(0, 5)
        last_ts = thread_ts
        for _ in range(n_replies):
            last_ts += random.uniform(60, 4 * 3600)  # 1 min – 4 hr later
            if last_ts > now:
                break
            conn.execute(
                "INSERT INTO posts (thread_id, author, body, created_at) VALUES (?, ?, ?, ?)",
                (thread_id, random.choice(USERS), random_reply_body(), last_ts),
            )
            posts_inserted += 1

    conn.commit()
    print(f"seeded {threads_inserted} threads / {posts_inserted} posts "
          f"({multilingual_count} non-English) -> {DB_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="drop + recreate tables")
    parser.add_argument("--count", type=int, default=50, help="how many threads to seed")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed for reproducibility")
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
