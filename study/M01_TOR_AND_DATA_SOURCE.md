# M01 — Tor, Hidden Services, Docker & the Forums (the data source)

> Files: `docker-compose.yml`, `tor_config/{Dockerfile,torrc,entrypoint.sh}`,
> `tor_config_silkvault/*`, `onion_service/{app.py,schema.sql,seed_data.py,Dockerfile}`,
> `onion_service_silkvault/*`, and `backend/scraper/client.py` (the SOCKS part).
> Concept sources: `PROJECT_BRAIN.md` §3, §4.1, §4.2.

---

## PART A — CONCEPTS

### A1. Why Tor exists
Normal internet: your IP is visible to the server, and your ISP sees who you talk to.
Tor hides **who talks to whom** by routing through multiple volunteer relays.

### A2. Onion routing in 5 lines
1. Client picks a path of relays: **guard → middle → exit** (3 hops).
2. It wraps the message in **3 layers of encryption** (one key per relay) — like an onion.
3. Each relay peels **one layer**, learns only the **previous** and **next** hop.
4. Guard knows *you* but not the destination; exit knows the destination but not *you*;
   nobody sees both.
5. The path is a **circuit**; it is rebuilt periodically.

### A3. Hidden (onion) services — what we actually use
A normal Tor user reaches the *public* internet via an **exit** relay. A **hidden service** is
different: the server lives *inside* Tor, so **no exit relay is used** and the server's IP is
hidden too (both sides anonymous). How a client reaches `abc…xyz.onion` (v3):

1. **Service setup:** the service picks **introduction points** (relays) and publishes a signed
   **descriptor** (listing them) to **HSDirs** (hash-ring of directory relays).
2. **Client** knows the `.onion` name → computes where the descriptor lives → downloads it.
3. Client builds a circuit to a **rendezvous point** (RP) — a random relay — and tells the
   service (via an intro point) "meet me at RP, here's a one-time secret".
4. Service builds its own circuit to the RP. RP splices the two circuits.
5. Result: a **~6-hop** path (3 from each side), end-to-end encrypted. Neither side learns the
   other's IP.

**v3 address format:** `base32( ed25519_public_key[32B] ‖ checksum[2B] ‖ version[1B=0x03] )`
= 35 bytes → **56 chars** + `.onion`. The address *is* the public key ⇒ self-authenticating
(you can't spoof it without the private key). Checksum = first 2 bytes of
`SHA3-256(".onion checksum" ‖ pubkey ‖ version)`. (v2 was 16 chars/RSA-1024 — deprecated/insecure.)

### A4. SOCKS5 and *proxy-side DNS* (an interview favourite)
- **SOCKS5** is a generic TCP proxy protocol. A client connects to the proxy (Tor listens on
  `:9050`) and says "connect me to HOST:PORT". It is *not* HTTP-specific.
- For `.onion` the **hostname must be resolved by Tor**, not by your OS — your DNS has no
  record for it (and a DNS lookup would also leak what you're visiting).
- SOCKS5 lets the client send the **hostname** (ATYP=domain) instead of an IP → proxy-side DNS.
  curl/requests call this scheme `socks5h://` ("h" = hostname resolved by proxy).
- **httpx** (via `socksio`) doesn't accept `socks5h://` but its SOCKS5 transport already sends
  the hostname to the proxy, so plain **`socks5://`** behaves like `socks5h://`.
  ⇒ This is documented in `client.py`'s docstring: *"Don't change this back to socks5h://"*.
  It's a real bug you hit — a good "hardest bug" story.

### A5. Why *synthetic* forums?
Scraping real darknet markets has **legal, ethical and availability problems** (illegal
content, can't redistribute data, sites vanish). But the **plumbing** — Tor circuit, SOCKS5,
`.onion` resolution, slow high-latency HTTP — is the same. So we host two fake forums as
**real** v3 hidden services: the scraper exercises the genuine path. Content is generated from
templates stuffed with fake IOCs so downstream stages have signal.

### A6. Why *two* forums? (important design intent)
| | DarkBay | SilkVault |
|---|---|---|
| Vocabulary | threads / posts | listings / messages |
| URLs | `/category/<slug>`, `/thread/<id>` | `/board/<slug>`, `/listing/<id>` |
| API | **`/api/posts?since=`** JSON | **none — HTML only** |
| Scraper path | `ForumClient` (JSON) | `HtmlForumClient` (BeautifulSoup) |
| Tor port on host | 9050 | 9051 |

Purpose of SilkVault: **prove the scraper isn't hard-coded to one site/format.** Real forums
don't offer a JSON API. The HTML client parses `data-*` attributes first (robust), falls back
to class names/`<time>` — the way you'd treat an unfamiliar site.

### A7. Docker / Compose concepts you'll be asked about
- **Image vs container**; Dockerfile = recipe; Compose = multi-container orchestration.
- **`expose` vs `ports`:** `expose: 5000` = reachable *only* inside the compose network (the
  forum is **not** published to your host!). `ports: "127.0.0.1:9050:9050"` publishes to the
  host, bound to **loopback only** (not your LAN).
- **Healthcheck + `depends_on: condition: service_healthy`:** Tor won't start until the Flask
  app answers `/healthz` (avoids Tor publishing a service whose backend isn't ready).
- **Volumes:** named volume `forum_data:/data` persists the forum's SQLite across restarts;
  **bind mount** `./tor_config/hidden_service:/var/lib/tor/sentinelx_forum` persists the
  **onion private key** ⇒ **stable `.onion` address** across rebuilds. (Lose the key = new address.)
- **`restart: unless-stopped`**, `exec` in CMD so gunicorn is PID 1 and gets SIGTERM.
- **Least privilege:** `entrypoint.sh` runs as root only to fix ownership, then `setpriv`
  drops to `debian-tor` before `exec tor`.

---

## PART B — THE CODE, FILE BY FILE

### B1. `tor_config/torrc` (the whole Tor config)
```
SOCKSPort 0.0.0.0:9050        # accept SOCKS5 from outside the container (published to host loopback)
SOCKSPolicy accept *          # allow any client that can reach the port
Log notice stdout             # `docker logs` works
DataDirectory /var/lib/tor
HiddenServiceDir /var/lib/tor/sentinelx_forum/   # keys + `hostname` file written here
HiddenServiceVersion 3
HiddenServicePort 80 forum:5000   # onion:80  →  container "forum" port 5000 (Compose DNS name)
ClientOnly 1                  # we're not a relay; only a client + onion-service publisher
```
`HiddenServicePort 80 forum:5000` is the key line: it's the *reverse-proxy mapping* from the
onion address to the Flask container.

### B2. `tor_config/entrypoint.sh` — the "why does this exist" file
- Tor **refuses to start** unless `HiddenServiceDir` is owned by the Tor user with mode `700`.
- On Docker Desktop (Windows/macOS), bind mounts come in **root-owned** whatever the image says.
- So: `mkdir -p`, `chown -R debian-tor`, `chmod 700`, then
  `exec setpriv --reuid=debian-tor --regid=debian-tor --init-groups tor -f /etc/tor/torrc`.
  (`setpriv` from `util-linux`, installed in the Dockerfile.)

### B3. `tor_config/Dockerfile`
`debian:bookworm-slim` → `apt-get install tor ca-certificates util-linux` (`--no-install-recommends`,
`rm -rf /var/lib/apt/lists/*` to keep the image small) → copy torrc + entrypoint → `ENTRYPOINT`.
Deliberately stays root until the entrypoint drops privileges.

### B4. `docker-compose.yml`
Four services: `forum`, `tor`, `silkvault`, `tor-sv`. SilkVault's Tor maps host `9051` →
container `9050` (so both Tors can coexist on one host). Each forum: healthcheck uses
`python -c urllib.request.urlopen('http://127.0.0.1:5000/healthz')` (no curl needed in slim image).

### B5. `onion_service/app.py` (DarkBay)
- Flask, SQLite per-request connection stored in `flask.g`, closed in `@teardown_appcontext`;
  `PRAGMA foreign_keys = ON` (SQLite default is **off**).
- HTML: `/`, `/category/<slug>`, `/thread/<int:id>`.
- **API used by the scraper:** `GET /api/posts?since=<epoch>&limit=&category=`
  → `SELECT … WHERE p.created_at > ? ORDER BY created_at ASC LIMIT ?` (strictly greater-than,
  ascending, limit capped at 1000). Returns `{count, since, now, posts[]}`.
  Parameterised SQL (`?`) ⇒ no SQL injection. Input parse errors fall back to defaults.
- `POST /api/threads` creates thread+post — used to **inject live demo content** so the whole
  pipeline lights up on genuinely new data.
- `/healthz` opens a fresh connection and runs `SELECT 1`.
- Timestamps = Unix epoch floats ⇒ numeric `since` comparison, no date parsing.

### B6. `onion_service/seed_data.py`
Template tuples `(category, title, body_template)` filled from pools of fake IPs, defanged
domains (`secure-update[.]xyz`), BTC bech32 addresses, sha256s, CVEs, actors, malware,
industries. `--seed 42` ⇒ reproducible; `--count 60`; `--reset`. Bodies intentionally IOC-rich.
Dockerfile CMD: *if DB file missing → seed, then `exec gunicorn -w 2 -b 0.0.0.0:5000`*.

### B7. `onion_service_silkvault/app.py`
Same patterns, but **only HTML**. `listing.html` emits
`<div class="vault-message" data-message-id=".." data-epoch="..">` + `<time datetime=ISO>` +
`.message-author` / `.message-body`. The `data-epoch` exists so the scraper gets a clean number.
`/new` is a plain HTML `<form>` POST (no JSON) → demo can post a thread in Tor Browser.

### B8. The Tor/SOCKS part of `scraper/client.py`
```python
DEFAULT_SOCKS_PROXY = "socks5://127.0.0.1:9050"
DEFAULT_TIMEOUT = httpx.Timeout(connect=30, read=60, write=30, pool=60)  # Tor is slow
self._client = httpx.Client(proxy=proxy, timeout=timeout, follow_redirects=False)
r = self._client.get(f"http://{onion}/api/posts", params={"since": since, "limit": limit})
```
- `http://` (not https): onion services are already end-to-end encrypted by Tor itself.
- `read_onion_hostname()` reads `tor_config/hidden_service/hostname` and validates it ends with
  `.onion`; raises a *helpful* error if the stack isn't up.
- Generous timeouts because circuit building + rendezvous add seconds of latency.

---

## PART C — CTO GRILL: QUESTIONS & MODEL ANSWERS

**Q1. How does your laptop reach a .onion address?**
The scraper uses httpx with a SOCKS5 proxy at 127.0.0.1:9050, which is a Tor daemon in a
container. The client sends the `.onion` hostname to Tor (proxy-side DNS). Tor fetches the
service descriptor from HSDirs, builds a circuit to a rendezvous point, the service builds one
too, they're joined — roughly six hops, end-to-end encrypted, no exit node.

**Q2. Why `socks5://` and not `socks5h://`?**
httpx doesn't recognise `socks5h`; its SOCKS5 implementation passes the hostname to the proxy
anyway, so `socks5://` already gives proxy-side resolution. Using local DNS would fail for
`.onion` and leak the lookup.

**Q3. Is the Tor part real or mocked?**
Real. The forums are genuine v3 hidden services with persistent keys; only the *content* is
synthetic. The same scraper would run against a real forum.

**Q4. Why host your own forum instead of scraping real ones?**
Legal/ethical exposure, illegal content, no redistribution rights, unreliable uptime, and no
reproducible test data. Synthetic gives deterministic (seeded) data with known IOCs.

**Q5. How do you keep the .onion address stable?**
The hidden-service directory (private key + hostname) is bind-mounted from the host; Tor
regenerates only if it's missing.

**Q6. Why does the entrypoint chown and drop privileges?**
Tor aborts if the HS dir isn't owned by its user with 0700; Docker Desktop bind mounts are
root-owned. So root fixes perms, then `setpriv` drops to `debian-tor` — least privilege for the
long-running process.

**Q7. `expose` vs `ports`?**
`expose` = internal network only (forum is invisible from the host); `ports` publishes. Only
the Tor SOCKS ports are published, and only on 127.0.0.1.

**Q8. Is SOCKS5 exposed to my LAN?** No — published as `127.0.0.1:9050:9050`. (Inside the
container Tor listens on 0.0.0.0 so the host-side port-forward can reach it.)

**Q9. Does Tor make your scraper anonymous? Is it safe for real scraping?**
Tor hides network identity, but OPSEC is bigger than that: use isolated VM, don't log in with
real identities, don't download/execute payloads, respect law/policy, avoid interacting with
actors. Also scraped text is untrusted → prompt injection (see M06).

**Q10. What's the latency/throughput impact?** Seconds per request; hence timeouts of 30/60 s
and the incremental `since` cursor so we never re-download history.

**Q11. How would you scale scraping?** Multiple Tor instances/circuits, a job queue, per-site
rate limiting & politeness, retry/backoff, headless-browser for JS sites, captcha strategy.

**Q12. Why two forums?** To prove the scraper generalises: JSON-API forum vs HTML-only forum.

**Q13. What does `HiddenServicePort 80 forum:5000` mean?** Requests to the onion on port 80 are
forwarded to host `forum` (Compose DNS) port 5000 inside the Docker network.

**Q14. What's a rendezvous point / introduction point / HSDir?** Intro points = where the
service can be contacted; HSDirs = distributed directory storing descriptors; RP = relay where
the two circuits meet so neither learns the other's location.

**Q15. Why gunicorn, not `flask run`?** Production WSGI server, multiple workers (`-w 2`),
proper signal handling; Flask's dev server isn't for production.

---

## PART D — QUIZ (answer aloud, then check)
1. Why can't your OS DNS resolve `.onion`? What does SOCKS5 do about it?
2. What's the difference between a Tor *exit* path and an onion-service path?
3. What would break if you deleted `tor_config/hidden_service/`?
4. Why are `forum` containers not reachable from your browser at localhost:5000?
5. In `/api/posts`, why `>` and not `>=` for `since`? (Hint: M02 — cursor/duplicates.)
6. Name 3 differences between DarkBay and SilkVault that force different scraper code.

**Answers:** (1) no DNS record, Tor resolves/rendezvous by key; SOCKS5 passes hostname to proxy.
(2) exit path leaves Tor to a public server; onion path stays inside Tor with a rendezvous, ~6
hops, no exit. (3) a new key → new `.onion` address; scraper's saved hostname/URL stale.
(4) only `expose`d, no `ports` publish. (5) `>` avoids re-fetching the last post we already
have; duplicates would still be dropped by UNIQUE. (6) API vs none, different URL schema,
different markup/vocabulary.

When you're comfortable → say **"M02"** (Scraper + cursor + database).
