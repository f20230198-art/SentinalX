# STAGE 01 — Synthetic .onion Forum + Tor Hidden Service

> **Read this on your own time.** It is the deep-dive companion to the code shipped in Stage 1. It exists because the build cadence for SentinelX is *build first, teach after*: the code is already working, this doc explains it.

---

## 0. What you can do now that Stage 1 is done

After `docker compose up --build` from the repo root:

1. Two containers are running: `sentinelx-forum` (Flask + gunicorn) and `sentinelx-tor` (Tor daemon).
2. The forum is **not** reachable on any host port. The only way in is through Tor.
3. The Tor container has published a v3 hidden service. Its address is in `tor_config/hidden_service/hostname` and it is **stable across rebuilds** because that directory is bind-mounted from the host.
4. You can open Tor Browser, paste the `.onion` address, and see a phpBB-circa-2005 darknet-style forum with ~60 threads / ~235 posts pre-seeded with realistic IOCs (IPs, CVEs, BTC addresses, SHA256 hashes, threat actor names, malware families).
5. The Tor container also exposes a SOCKS5 proxy on `127.0.0.1:9050` of the host. Stage 2's scraper will route through that proxy to fetch the forum's content as if it were a real darknet site.

That last point is the whole reason this stage exists. We are not building a forum to publish — we are building **a controlled training ground** that makes the scraper exercise the same SOCKS5 + `.onion` plumbing it would use against a real darknet site, with none of the legal / ethical / availability problems.

---

## 1. The big picture

```
┌─────────────────────────────────────────────────────────────────┐
│                       Docker host (your laptop)                 │
│                                                                 │
│   ┌──────────────────────┐     compose network    ┌───────────┐ │
│   │  sentinelx-tor       │◀──────────────────────▶│ sentinelx-│ │
│   │  - tor daemon        │     forum:5000 (HTTP)  │   forum   │ │
│   │  - SOCKS5 :9050      │                        │  Flask +  │ │
│   │  - HS publisher      │                        │  gunicorn │ │
│   └──────────────────────┘                        └───────────┘ │
│            │   ▲                                                │
│            │   │ bind mount                                     │
│            │   │ ./tor_config/hidden_service/                   │
│            │   │     hs_ed25519_*  + hostname                   │
│            │   ▼                                                │
│      127.0.0.1:9050  ◀── future scraper hits this proxy         │
└────────────┬────────────────────────────────────────────────────┘
             │
             │ Tor circuits (3 hops, encrypted)
             ▼
       Tor network ──▶ rendezvous point ──▶ same hidden service
```

Two containers, one published `.onion`, one bind mount for keypair persistence, one SOCKS port for the next stage. That is the entire surface area.

---

## 2. Files and what each does

### 2.1 `onion_service/` — the forum

| File | Purpose |
|------|---------|
| `app.py` | Flask app: HTML routes, JSON API, healthcheck. |
| `schema.sql` | Two tables — `threads`, `posts`. Epoch-float timestamps. |
| `seed_data.py` | Generates 50–80 realistic darknet threads with IOC-rich posts and 0–5 replies each. |
| `templates/` | Jinja2 templates (`base.html`, `index.html`, `category.html`, `thread.html`). |
| `static/style.css` | Dark theme: black background, green/orange accents, monospace, 1990s/2000s phpBB vibe. |
| `requirements.txt` | `Flask==3.0.3`, `gunicorn==22.0.0`. Two pins, nothing else. |
| `Dockerfile` | `python:3.12-slim` → install deps → seed-on-first-boot → gunicorn. |
| `.dockerignore` | Keeps `forum.db`, `__pycache__`, `.venv` out of the image. |

### 2.2 `tor_config/` — the Tor sidecar

| File | Purpose |
|------|---------|
| `torrc` | SOCKS5 on `0.0.0.0:9050`, v3 hidden service forwarding `:80 → forum:5000`, `ClientOnly 1`. |
| `Dockerfile` | `debian:bookworm-slim` + `tor` + `util-linux` (for `setpriv`). |
| `entrypoint.sh` | Fixes bind-mount ownership at runtime, drops to `debian-tor`, exec's `tor`. |
| `hidden_service/` | **Gitignored.** Holds `hs_ed25519_secret_key`, `hs_ed25519_public_key`, `hostname`. |

### 2.3 `docker-compose.yml`

Wires the two services together:

- `forum` — built from `./onion_service/`, no host port (only `expose: 5000` to the compose network), named volume `forum_data` mounted at `/data`, healthcheck that hits `/healthz`.
- `tor` — built from `./tor_config/`, `depends_on` the forum being healthy, exposes `127.0.0.1:9050` to the host, bind-mounts `./tor_config/hidden_service` to `/var/lib/tor/sentinelx_forum` for keypair persistence.

---

## 3. How each piece works

### 3.1 The Flask app (`app.py`)

**Connection per request, attached to `flask.g`.** The standard Flask idiom:

```python
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db
```

`g` is a per-request namespace. `@app.teardown_appcontext` closes the connection when the request finishes. We set `row_factory = sqlite3.Row` so query results are dict-like (`row["title"]`) instead of plain tuples. We also turn on foreign keys explicitly because **SQLite ships with `PRAGMA foreign_keys = OFF` by default** for backwards compatibility — without that one line, the `ON DELETE CASCADE` in the schema is silently ignored.

**HTML routes** are conventional Flask:

- `/` — front page, lists 50 most recently active threads (`ORDER BY COALESCE(MAX(p.created_at), t.created_at) DESC`) plus a category sidebar.
- `/category/<slug>` — same but filtered.
- `/thread/<int:thread_id>` — a single thread with all its posts in chronological order.

Why `COALESCE`? Because a freshly-created thread has no replies yet, so `MAX(p.created_at)` returns `NULL`. We fall back to the thread's own `created_at` so empty threads still sort sensibly.

**JSON API** — this is what the Stage 2 scraper will actually hit:

- `GET /api/posts?since=<epoch>&limit=&category=` — lists posts strictly newer than `since`. The scraper uses this to do **incremental polling**: remember the last `now` it received, send it back as `since` next time, only get new posts.
- `GET /api/post/<id>` — single post detail.
- `GET /healthz` — used by the docker healthcheck. Opens a fresh sqlite connection (not the `g`-attached one — `healthz` may run in a worker that has no request context yet) and runs `SELECT 1`.

**`since=<epoch>` over cursor pagination.** A cursor pagination scheme (e.g. `?after_id=123`) is more robust against backfills, but `since` is dead simple, the dataset is bounded (a synthetic forum), and incremental polling is *exactly* the access pattern real darknet scrapers use against real forum APIs. Picking the realistic option.

**`int()` + `float()` parsing wrapped in `try/except`.** If a client sends `?since=banana` we silently fall back to `0.0` rather than 400-ing. The scraper that will hit this is ours, but defaulting to permissive parsing avoids a class of silly outages later.

**`limit` capped at 1000.** Small but important — without the cap, a single request can drag the entire forum table.

**`@app.template_filter("fmt_ts")`.** Templates do `{{ thread.created_at | fmt_ts }}`. The filter converts an epoch float into a human-readable UTC string. Defining it server-side keeps templates dumb — they don't know what a unix timestamp is, they just call the filter.

### 3.2 The schema (`schema.sql`)

Two tables, three indexes, and one `ON DELETE CASCADE`. Decisions:

- **`created_at REAL`** — epoch seconds as a float, not an ISO-8601 string. Makes the JSON `since` filter a numeric comparison instead of a date parse. This is the same reason most timeseries databases store time as int64 nanoseconds: comparing numbers is faster and unambiguous.
- **Indexes on `posts(thread_id)`, `posts(created_at)`, `threads(category)`.** The three columns we filter on. Without `idx_posts_created`, `WHERE p.created_at > ?` becomes a full table scan — slow at 235 posts, catastrophic at 235k.
- **`FOREIGN KEY ... ON DELETE CASCADE`.** Delete a thread, the posts go with it. Only takes effect because we turned on `PRAGMA foreign_keys` in `app.py`.

### 3.3 The seeder (`seed_data.py`)

Generates believable darknet posts by template-substituting from pools:

- **IOC pools** — `FAKE_IPS`, `FAKE_DOMAINS` (with the bracket-defang convention `secure-update[.]xyz` that real CTI feeds use), `FAKE_BTC`, `FAKE_SHA256`, `FAKE_CVES`, `ACTORS`, `MALWARE`, `INDUSTRIES`, plus a `USERS` pool of plausible darknet handles.
- **Templates** — 10 thread templates (`(category, title_tpl, body_tpl)` triples) covering access sales, credential dumps, vuln PoCs, malware loaders, phishing kits, threat-intel chatter. 10 reply templates for replies. All written in plausible cybercrime-forum vernacular.
- **`random_post_body(category, ts)`** — picks a template whose category matches, formats it with random picks from each pool, returns `(title, body)`.
- **`seed(conn, n_threads)`** — for each thread, picks a category + author, picks a creation timestamp uniformly in the last 14 days, writes the OP, then writes 0–5 replies each strictly after the OP and capped at "now."
- **`--reset` flag** — drops + recreates tables. Without it the seeder appends to whatever is already there.
- **`--seed` flag** — RNG seed for reproducibility. The Dockerfile uses `--seed 42` so every fresh container generates the same dataset.

The reason the IOCs are realistic-looking is that **Stage 3** (NER + regex IOC extraction) and **Stage 4** (LLM summarization) need signal to chew on. If the seeder produced lorem ipsum, the rest of the pipeline would have nothing to extract.

### 3.4 The Tor `torrc`

Five directives that matter:

```
SOCKSPort 0.0.0.0:9050
SOCKSPolicy accept *
HiddenServiceDir /var/lib/tor/sentinelx_forum/
HiddenServiceVersion 3
HiddenServicePort 80 forum:5000
ClientOnly 1
```

- **`SOCKSPort 0.0.0.0:9050`** — bind on all interfaces inside the container so Docker can publish it. We then bind it on the host only to `127.0.0.1` (in `docker-compose.yml`) so it isn't reachable from the LAN.
- **`SOCKSPolicy accept *`** — allow any client to use the proxy. Inside a single-tenant container behind a loopback bind, this is fine. **Never do this on a public Tor instance** — you'd be running an open SOCKS proxy.
- **`HiddenServiceDir`** — where Tor stores the v3 keypair (`hs_ed25519_secret_key`, `hs_ed25519_public_key`) and writes the resulting `hostname` file (the `.onion` address derived from the public key). Bind-mounting this directory to the host is what makes the address stable across rebuilds.
- **`HiddenServiceVersion 3`** — v3 onions (56-char addresses, ed25519 keys). v2 was deprecated in 2021. Don't pick v2.
- **`HiddenServicePort 80 forum:5000`** — when a client hits the `.onion` on port 80, Tor forwards the connection to `forum:5000` on the compose network. The `forum` hostname is resolved by Docker's embedded DNS.
- **`ClientOnly 1`** — we are not relaying other people's traffic. Without this directive, Tor *could* opportunistically participate in the relay network. We don't want that for a CTI sandbox.

### 3.5 The Tor entrypoint

This is the single trickiest piece in the stage. Read it carefully because the same pattern shows up over and over in production Docker:

```sh
HS_DIR=/var/lib/tor/sentinelx_forum
mkdir -p "$HS_DIR"
chown -R debian-tor:debian-tor "$HS_DIR"
chmod 700 "$HS_DIR"
chown -R debian-tor:debian-tor /var/lib/tor

exec setpriv --reuid=debian-tor --regid=debian-tor --init-groups \
    tor -f /etc/tor/torrc
```

**Why we need this.** Tor's hidden-service code refuses to use a `HiddenServiceDir` unless it is owned by the user Tor is running as, with mode `0700` — sensible, because the `hs_ed25519_secret_key` inside it is the *identity* of your `.onion` and anyone who reads it can impersonate you. If the dir is `0755` or owned by someone else, Tor logs `is not owned by this user (debian-tor) but by root` and exits.

**Why the `chown` in the Dockerfile isn't enough.** When you bind-mount a host directory into a container on Docker Desktop (Windows / macOS), the mount comes in as `root:root` regardless of what the image set at build time. Image-time `chown` is invisible to the bind mount. So we have to fix ownership *at runtime*.

**Why we start as root and drop privileges.** Only root can `chown`. We need root long enough to fix ownership, then we drop to `debian-tor` so Tor itself isn't running as root. `setpriv` (from `util-linux`) is the modern way to do this — `su` and `gosu` work too, but `setpriv` is the small, kernel-supported primitive. `--reuid` / `--regid` set the real and effective UID/GID; `--init-groups` populates the supplementary group list as if logging in as that user.

**`exec`** replaces the shell with `tor`, so `tor` becomes PID 1 of the container. That matters because Docker sends `SIGTERM` to PID 1 on `docker stop`. If you forgot the `exec`, the shell would catch the signal and tor would only get killed when the shell timed out and Docker escalated to `SIGKILL` — slow shutdowns, possibly truncated state.

This pattern — *start as root, fix permissions or write secrets, drop privileges, exec the real process* — is the standard answer to bind-mount ownership in container land. Worth memorising.

### 3.6 The Dockerfile pair

**Forum image** (`onion_service/Dockerfile`):

```dockerfile
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 FORUM_DB=/data/forum.db
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 5000
CMD ["sh", "-c", "if [ ! -f \"$FORUM_DB\" ]; then python seed_data.py --reset --count 60 --seed 42; fi && exec gunicorn -w 2 -b 0.0.0.0:5000 --access-logfile - app:app"]
```

Notes:
- `requirements.txt` is copied **before** the rest of the source so its layer caches independently. Editing `app.py` doesn't bust the pip layer.
- `PYTHONUNBUFFERED=1` makes `print` and gunicorn logs flush immediately — without it, Docker's log driver lags.
- `PYTHONDONTWRITEBYTECODE=1` prevents `__pycache__` from polluting the image.
- The CMD is a shell because we need conditional logic ("seed only if DB missing"). The `exec gunicorn` at the end matters for the same SIGTERM reason as the Tor entrypoint.
- `gunicorn -w 2` = 2 worker processes. Two is plenty for a synthetic forum being scraped by one client.
- `--access-logfile -` writes access logs to stdout so `docker logs` shows them.

**Tor image** (`tor_config/Dockerfile`): standard debian-slim, install `tor` + `ca-certificates` + `util-linux`. Runs as root (the entrypoint drops privileges).

### 3.7 `docker-compose.yml`

The few non-obvious bits:

- **`expose` vs `ports`**: `expose: 5000` only opens the port within the compose network, not on the host. The forum is *only* reachable via Tor — that's the entire point. The Tor container, in contrast, uses `ports: "127.0.0.1:9050:9050"` to publish SOCKS5 on the host's loopback so the future scraper (running on the host or in another container) can use it.
- **`depends_on: condition: service_healthy`**: tor doesn't start until forum's healthcheck passes. Without this, tor would publish a hidden service that points at a forum that isn't ready yet, and the first request through the `.onion` would 502.
- **Volume vs bind mount**:
  - `forum_data` is a **named volume** — Docker manages it under `/var/lib/docker/volumes/...`. Reseeding requires `docker compose down -v`.
  - `./tor_config/hidden_service` is a **bind mount** — the host directory *is* the container directory. Survives `down -v`. This is on purpose: we want the `.onion` address to outlive `down -v` so we don't have to re-paste it into the scraper config every time we wipe the forum DB.

---

## 4. Why these choices over the alternatives

| Decision | Alternative | Why we picked this |
|----------|-------------|-------------------|
| Flask + SQLite | FastAPI + Postgres | Stage 1 is a fixture, not an API product. Flask + SQLite is one container, no migration tooling, no separate DB process. We spend the complexity budget on the Tor side, where the learning is. FastAPI shows up in Stage 6 where async + OpenAPI matter. |
| gunicorn (sync workers) | uvicorn / uWSGI | Flask is WSGI, gunicorn is the obvious WSGI server, two workers handle a single-scraper workload trivially. uvicorn is for ASGI (async) — overkill here. |
| v3 hidden service | v2 onions | v2 is removed from the Tor network. v3 is the only option in 2026. |
| Bind-mount keypair | Regenerate per build | Stable `.onion` means scraper config doesn't churn. The keypair is gitignored so it never lands in git, but it persists locally. |
| `setpriv` to drop privileges | `gosu` / `su-exec` | `setpriv` is in `util-linux`, already in Debian, no extra binary to install. `gosu` is fine but is an extra dependency. |
| Realistic IOCs in seeded posts | Lorem ipsum | NER + LLM stages downstream need signal. A forum that says "the quick brown fox" would teach us nothing about IOC extraction. |
| Two containers (forum + tor) | One container running both | Single-responsibility containers. `docker compose down` of one doesn't touch the other. The forum image stays clean Python; the Tor image stays clean Debian. Mixing them would mean one bloated image and confused logs. |
| Compose network DNS (`forum:5000`) | Hardcoded IP | Docker's embedded DNS resolves service names. IPs change across `up`/`down`; service names don't. |
| `ClientOnly 1` | Default (relay-eligible) | We're a CTI lab, not a Tor volunteer. We don't want our laptop relaying anyone else's traffic. |
| Epoch-float timestamps | ISO-8601 strings | `WHERE created_at > ?` is a numeric compare. No timezone parsing. The `fmt_ts` Jinja filter handles human display. |

---

## 5. Tech-stack tour and where each piece lives in industry

### 5.1 Flask
A WSGI microframework. Minimal surface — routes, request, jsonify, templates, that's most of it. **In industry:** still extremely common for internal tools, admin dashboards, ML model-serving endpoints (the original "load a pickled model and serve `/predict`" pattern). Has ceded ground to FastAPI for new public APIs because async + automatic OpenAPI docs are a big quality-of-life win. Most large Python orgs (Pinterest, LinkedIn, parts of Netflix, much of Reddit's old stack) had Flask somewhere.

### 5.2 gunicorn
A pre-fork WSGI HTTP server. Spawns N worker processes; each one runs the WSGI app. **In industry:** the default Python web app server. You'll see it behind nginx in basically every Python web deployment — Django, Flask, FastAPI in WSGI mode. The number-of-workers rule of thumb is `2 * cpu_cores + 1` for CPU-bound; we use 2 because this is a fixture.

### 5.3 SQLite
A serverless file-based SQL database. The whole DB is one file. **In industry:** the most-deployed database in the world by install count (every Android phone, every iOS app, every browser, every edge device). Production-appropriate for read-heavy and single-writer workloads up to roughly hundreds of GB. CTI tooling specifically: a lot of intel ingestion/exploration scripts use SQLite as a checkpoint store. We will outgrow it nowhere in this project — the entire SentinelX dataset will live happily in one SQLite file.

### 5.4 Jinja2
Server-side template engine. `{{ ... }}` for expressions, `{% ... %}` for control flow, autoescape on for HTML. **In industry:** Flask, FastAPI's `Jinja2Templates`, Ansible playbooks (yes, the same Jinja2), Salt states, dbt models (`{{ ref('foo') }}`), Home Assistant configs. If you know Jinja2 you can read half the YAML in DevOps land.

### 5.5 Tor (the daemon)
The reference implementation of the Tor protocol. **What it actually does:** maintains an encrypted connection to three relays (a "circuit"), tunnels TCP through that circuit, and (for hidden services) participates in the rendezvous protocol that lets clients reach a server without knowing its IP. **In industry / research:** journalists' SecureDrop, whistleblowing platforms, dark web marketplaces (ours is a synthetic), and *every CTI tool that monitors darknet sources*. Recorded Future, Flashpoint, Intel471, KELA — they all run Tor scrapers. The plumbing we set up here (SOCKS5 + `.onion` DNS resolution via the proxy) is exactly what those tools use, just at much larger scale.

### 5.6 v3 hidden services
Onion address = base32(public ed25519 key + checksum + version byte). 56 characters. The address *is* the identity — there's no CA, no DNS. If you have the secret key file, you are that `.onion`. This is why `chmod 700` matters and why our keypair is gitignored. **Industry:** Facebook (`facebookcorewwwi.onion` — yes, really), the New York Times, ProPublica, BBC News all run public hidden services for users in censored regions. Behind the scenes, the rendezvous-point protocol is *the* clever cryptographic primitive that makes server-side anonymity possible.

### 5.7 Docker + Docker Compose
Linux containers (cgroups + namespaces) with an image format and a registry protocol. Compose is the tiny orchestrator for a single-host multi-container app. **Industry:** containers eat the world. Every cloud-native app on Earth ships as a container image. Compose itself is for local dev and small single-host deployments — at scale, Kubernetes or ECS or Nomad takes over, but the *image* you build with Docker is the same artifact those orchestrators run.

### 5.8 Bind mounts vs named volumes
Both are how Docker exposes a directory inside a container. **Bind mount**: a host path is mounted into the container — you can `cd` to it from the host, edit files, see them inside the container immediately. Used for source-code-during-dev and for stable artifacts you want to keep visible (our `.onion` keypair). **Named volume**: Docker owns the storage. Faster on Docker Desktop because it lives inside the Linux VM rather than crossing the host filesystem boundary. Used for databases (our `forum_data`).

### 5.9 setpriv / gosu / dropping privileges
The "container runs as root and drops to a non-root user before exec'ing the real process" pattern is **the** answer to bind-mount ownership traps. If you ever see `Permission denied` or `not owned by this user` from inside a container, it is almost always this. Real-world examples: every official Postgres image does this (`docker-entrypoint.sh` → `gosu postgres postgres ...`), Redis does this, Elasticsearch does this. We do it for the same reason.

### 5.10 Healthchecks + `depends_on: condition: service_healthy`
Compose-native dependency ordering. **Industry:** in Kubernetes the equivalent is readiness probes + initContainers / startup probes; in Nomad it's the `check` stanza + `restart`. The shape is identical: don't route traffic to a thing until it tells you it's ready.

---

## 6. Industry context: how does Stage 1 map to a real CTI shop?

Real CTI vendors have a "darknet collection" team whose entire job is the equivalent of Stages 1–2:

1. **Source acquisition** — they curate a list of hundreds-to-thousands of `.onion` URLs of interest (forums, marketplaces, leak sites, Telegram-mirror bridges).
2. **Crawler infrastructure** — distributed Tor exit pools (because real darknet sites rate-limit by circuit), captcha-solving fallbacks, account-management for forums that require login, anti-bot evasion.
3. **Storage** — every page raw + parsed, deduplicated by content hash, indexed for full-text search.
4. **Pipeline** — the same NER/LLM/MITRE stages we are about to build, but at industrial scale.

Our Stage 1 is the **fixture** that lets us learn stages 2–8 without doing any of the legal / ethical / operational hardening real darknet collection requires. The plumbing — SOCKS5 + `.onion` resolution + incremental polling — is identical to the real thing.

A useful test: when Stage 2's scraper points at our `.onion`, the only thing that changes for it to work against a real darknet forum is the URL list. That's the contract this stage is honouring.

---

## 7. Gotchas hit during the build (and how to avoid them next time)

### 7.1 Bind-mount ownership on Docker Desktop
**Symptom:** Tor crash-loops with `/var/lib/tor/sentinelx_forum/ is not owned by this user (debian-tor) but by root`.
**Cause:** Bind mounts on Docker Desktop come in as `root:root` regardless of image-time `chown`.
**Fix:** Runtime entrypoint that `chown`s + `setpriv`s. Already in `tor_config/entrypoint.sh`.
**Don't:** Revert to a `USER debian-tor` directive in the Dockerfile. It only works without a bind mount.

### 7.2 Transient network failures during apt/pip
**Symptom:** First `docker compose up --build` died mid-`apt-get install`.
**Fix:** Run it again. The base image was already cached from the failed attempt, so retry was cheap.
**Lesson:** Image builds are idempotent if you don't tag-and-discard between attempts. Don't `--no-cache` unless you have a reason.

### 7.3 `Socks version 71 not recognized` warnings
**Symptom:** Spam in tor logs.
**Cause:** Some local process probes 9050 as if it were HTTP. Tor's SOCKS handler correctly rejects.
**Fix:** Ignore. Reappears if a real client breaks — we'll know because *our* scraper would also fail.

### 7.4 OneDrive + Docker
The repo lives under a OneDrive-synced folder. OneDrive can lock files mid-write. If a Docker build fails with weird I/O errors, pause OneDrive sync and retry. Don't move the repo without checking with the user.

### 7.5 Forum DB persistence vs `.onion` persistence are decoupled on purpose
- `forum_data` (named volume) → reseed needs `docker compose down -v`.
- `tor_config/hidden_service/` (bind mount) → survives `down -v`. The `.onion` address is stable.

If you find yourself wanting to wipe the `.onion` and start over, manually `rm -rf tor_config/hidden_service/*`. Don't rely on `down -v` to do it.

---

## 8. What Stage 2 will look like (so you can see why Stage 1 was shaped this way)

Stage 2 builds the **scraper**:

- Reads the `.onion` address from `tor_config/hidden_service/hostname`.
- Uses `requests` (or `httpx`) with a `socks5h://127.0.0.1:9050` proxy — the `h` makes the *proxy* do the DNS lookup, which is what you need for `.onion` names (your local resolver doesn't know how to resolve them).
- Polls `GET /api/posts?since=<last_seen>` on a loop, persists each new post into a `raw_posts` table in `backend/db/`, dedups by `(thread_id, post_id)`.
- Surfaces a CLI: `python -m scraper.run --once`, `--watch`, `--reset-cursor`.

**Notice how Stage 1 makes Stage 2 trivial.** The scraper just hits a URL through SOCKS5; everything else (the corpus, the pagination cursor, the realistic content) was set up here. That's the payoff for building the fixture properly.

---

## 9. If you remember nothing else, remember these five things

1. **`.onion` resolution must happen in the proxy, not on your local resolver.** Use `socks5h://`, not `socks5://`. (h = "hostname through the proxy.")
2. **Hidden-service keypair = identity.** Mode 700, owned by the running user, never in git, bind-mounted for persistence.
3. **Bind mount on Docker Desktop = `root:root` at runtime.** Fix in entrypoint, drop privileges, exec the real process.
4. **Compose `depends_on: condition: service_healthy` is the only `depends_on` worth using.** The default just waits for the container to start, which doesn't mean the app inside it is ready.
5. **Stage 1 is a fixture for the whole pipeline.** Every later stage assumes a stable `.onion`, an incremental-poll JSON API, and IOC-rich content. We didn't pick those for fun — they're the contract Stages 2–8 will lean on.

---

**End of Stage 1 LEARN.** Stage 2 begins by writing the scraper. CLAUDE.md §3 and PROGRESS.md will get the status update next.
