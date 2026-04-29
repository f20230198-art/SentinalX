# STAGE 01 — Synthetic .onion Forum + Tor Hidden Service

> **Read this on your own time.** This is the deep-dive companion to the code shipped in Stage 1. The build cadence is *build first, teach after*: the code is already running, this doc is here to make you actually *understand* every choice in it.
>
> **How to read this:** linearly is fine, but each section is self-contained. If you hit a "Detour" or "Try this" box and you're tired, skip them — they're optional depth.

---

## Quick orientation: what does this whole stage do, in one paragraph?

We built a fake darknet forum on your laptop. It runs inside Docker. We then run a second Docker container alongside it called Tor, which publishes that forum as a real `.onion` address — the kind you can open in Tor Browser. Why fake? Because the next stages of this project (scraping, IOC extraction, LLM analysis, dashboard) need *something* to scrape, and scraping real darknet forums is a legal/ethical landmine. So we build our own miniature one, populate it with realistic-looking criminal posts (fake IPs, CVEs, BTC addresses), and let our scraper hit it through Tor as if it were the real thing. **The plumbing is 100% real. Only the data is synthetic.**

---

## 0. What you can do *right now* that Stage 1 is done

After running `docker compose up --build` from the repo root, the following are true:

1. Two containers are running side by side:
   - **`sentinelx-forum`** — the actual web forum (Flask app + a small server called gunicorn).
   - **`sentinelx-tor`** — the Tor daemon, which makes the forum reachable as a `.onion`.

2. **The forum is NOT reachable on `http://localhost:5000`.** This is on purpose. The only way to reach it is through Tor. (If you've done any pentest CTFs where a service is "internal-only" — same idea. The forum is bound to a private network, not your host.)

3. The Tor container has published a **v3 hidden service**. Its address is sitting in `tor_config/hidden_service/hostname` on your laptop. It looks like `ryntxkx...apyd.onion` (56 chars).

4. You can paste that `.onion` into **Tor Browser** and see a 2005-era phpBB-style darknet forum, with about 60 threads and 235 posts, all containing realistic-looking IOCs (Indicators of Compromise — the same atoms you'd see in a real CTI report: IPs, CVE numbers, hashes, BTC wallets, malware names).

5. The Tor container also exposes a **SOCKS5 proxy** on `127.0.0.1:9050` of your host. (SOCKS5 is just "a generic protocol for proxying TCP connections" — like an HTTP proxy but lower-level and protocol-agnostic. We'll use it in Stage 2 to make our Python scraper talk to the `.onion`.)

> **Try this now:**
> ```bash
> # See the .onion address
> cat tor_config/hidden_service/hostname
>
> # See both containers running
> docker ps
> ```

That last point — the SOCKS5 proxy — is *the whole reason this stage exists*. We are not building a forum to publish on the real darknet. We're building **a controlled training ground** so the next stage's scraper is forced to use the same SOCKS5 + `.onion` plumbing it would use against a real darknet site, with none of the legal/ethical/availability problems.

---

## 1. The big picture (one diagram, then the rest of the doc explains it)

```
┌──────────────────────────────────────────────────────────────────────┐
│                       Docker host (your laptop)                      │
│                                                                      │
│   ┌──────────────────────┐     compose network    ┌────────────────┐ │
│   │  sentinelx-tor       │◀──────────────────────▶│ sentinelx-forum│ │
│   │  - tor daemon        │     forum:5000 (HTTP)  │  Flask +       │ │
│   │  - SOCKS5 :9050      │                        │  gunicorn      │ │
│   │  - HS publisher      │                        │                │ │
│   └──────────────────────┘                        └────────────────┘ │
│            │   ▲                                                     │
│            │   │ bind mount                                          │
│            │   │ ./tor_config/hidden_service/                        │
│            │   │     hs_ed25519_*  + hostname                        │
│            │   ▼                                                     │
│      127.0.0.1:9050  ◀── future scraper hits this proxy              │
└────────────┬─────────────────────────────────────────────────────────┘
             │
             │ Tor circuits (3 encrypted hops)
             ▼
       Tor network ──▶ rendezvous point ──▶ same hidden service
```

What this shows:
- Two containers, talking to each other on a private "compose network" (an internal Docker network only those containers can see).
- The forum container *only* listens on that internal network — your host can't hit it directly.
- The Tor container can hit the forum on its internal hostname `forum:5000`. Tor knows how to forward `.onion` traffic back to that internal address.
- A bind mount (a folder shared between host and container) saves the Tor identity files outside the container so they survive rebuilds.
- The Tor container *also* exposes a SOCKS5 proxy on your host's loopback (`127.0.0.1:9050`). Stage 2's scraper will dial through that.

> **Detour: what's a "loopback" again?** `127.0.0.1` is your machine talking to itself. Anything bound to it is *not* reachable from your wifi network or anywhere else — only from the same machine. This is the same reason you bind dev servers to localhost when you're paranoid about exposing them.

Two containers, one published `.onion`, one bind mount for keypair persistence, one SOCKS port for the next stage. **That's the entire surface area.**

---

## 2. Files and what each does (a map before we go deep)

### 2.1 `onion_service/` — the forum itself

| File | Purpose |
|------|---------|
| `app.py` | The Flask app. Defines URL routes, JSON API, healthcheck. |
| `schema.sql` | DB structure — two tables: `threads`, `posts`. |
| `seed_data.py` | Generates 50–80 realistic darknet threads with IOC-rich post bodies. |
| `templates/` | Jinja2 templates (the HTML files Flask renders). |
| `static/style.css` | The black/green/orange phpBB-2005 visual style. |
| `requirements.txt` | Just `Flask` and `gunicorn`. Two pinned versions, nothing else. |
| `Dockerfile` | Recipe for building the forum container image. |
| `.dockerignore` | "Don't copy these files into the image" — keeps `forum.db`, `__pycache__`, etc. out. |

### 2.2 `tor_config/` — the Tor sidecar

| File | Purpose |
|------|---------|
| `torrc` | Tor's config file. Five settings, all important. |
| `Dockerfile` | Recipe for the Tor container image. |
| `entrypoint.sh` | Shell script that runs first when the Tor container starts. Fixes file permissions, then launches Tor as a non-root user. |
| `hidden_service/` | **Gitignored.** Holds the Tor identity files: `hs_ed25519_secret_key`, `hs_ed25519_public_key`, `hostname`. |

### 2.3 `docker-compose.yml`

The "wiring diagram" that tells Docker how to run both containers together. Defines the private network, the bind mounts, the volumes, the healthcheck, and the dependency order.

---

## 3. How each piece works (the meaty section)

### 3.1 The Flask app (`app.py`)

#### What is Flask doing here?

Flask is a minimal Python web framework. You define functions, decorate them with URL paths, and Flask routes incoming HTTP requests to the right function. Its only job in Stage 1 is to serve forum pages and a JSON API.

#### One DB connection per request

```python
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db
```

Three things happening here, each worth knowing:

- **`g`** is Flask's "globals for this request" object — a temporary dict that gets thrown away when the request finishes. So `g.db` is *this request's* DB connection, not a shared one. (Why? Because `sqlite3` connections are not safe to share across threads, and gunicorn runs multiple worker processes and threads.)
- **`row_factory = sqlite3.Row`** — by default, SQLite returns each row as a tuple like `("Alice", 42)`. With `Row`, you get a dict-like thing where you can write `row["name"]` instead of `row[0]`. Way easier to read.
- **`PRAGMA foreign_keys = ON`** — this is a quirk you have to know. SQLite *ships with foreign keys disabled by default*, for backwards compatibility with ancient SQLite versions. So if your `schema.sql` says `FOREIGN KEY ... ON DELETE CASCADE` and you don't run this PRAGMA, the database will silently ignore it. Your "delete a thread → its posts get deleted too" rule will not work. This is a classic gotcha.

> **Detour: PRAGMA?** A PRAGMA is a SQLite-specific command that changes the behaviour of the database itself, not your data. Think of it like a `--flag` you'd pass to a program at startup. Other databases (Postgres, MySQL) don't use this word — they have their own equivalents. SQLite is unusual in having a lot of behaviour controlled by these.

`@app.teardown_appcontext` is registered elsewhere to close the connection when the request finishes. (Closing matters — leaked connections are how server processes slowly run out of file handles.)

#### HTML routes

Three of them, all standard Flask:

- **`/`** — the front page. Lists the 50 most recently active threads, plus a sidebar with category counts.
- **`/category/<slug>`** — same as `/`, but filtered to one category like `marketplace` or `vulnerabilities`.
- **`/thread/<int:thread_id>`** — a single thread with all its posts in chronological order.

The "<int:thread_id>" syntax is a Flask URL converter — it tells Flask "this segment must be an integer, parse it, pass it as `thread_id`." If someone hits `/thread/banana`, Flask returns 404 before even calling our function. (You may have *exploited* the absence of this kind of validation in IDOR bugs — when an app trusts whatever string the user puts in a URL.)

#### One subtle detail: the `COALESCE` for sorting

```sql
ORDER BY COALESCE(MAX(p.created_at), t.created_at) DESC
```

`COALESCE(a, b)` is "use `a` if it's not NULL, otherwise use `b`." Here we want to sort threads by "when was the last post in this thread?" — but a brand-new thread has zero posts, so `MAX(post.created_at)` is NULL. `COALESCE` falls back to the thread's own creation timestamp so empty threads still sort sensibly (instead of getting NULL and being shoved to the bottom).

#### The JSON API (this is what Stage 2 will actually scrape)

```
GET /api/posts?since=<epoch>&limit=&category=
GET /api/post/<id>
GET /healthz
```

The first endpoint is the heart of the contract with Stage 2:

> **`since=<epoch>`** — give me all posts strictly newer than this timestamp.

This is called **incremental polling**. The scraper remembers the most recent timestamp it has seen, and on the next poll asks for everything after that. The forum returns only the new posts. This means the scraper never re-downloads the entire forum — it just catches up on the delta.

> **Detour: Why epoch float for timestamps?**
> An "epoch float" is just `time.time()` — a number like `1777120704.17`, the seconds since 1970-01-01. We could've stored ISO-8601 strings (`"2026-04-27T12:38:24Z"`) but then `WHERE created_at > '2026-04-27T...'` becomes a string comparison, and timezones become a parsing problem. Storing as a float means filtering is just `WHERE created_at > 1777120704.17`. Fast, unambiguous, no parsing. Most time-series databases pick this same shape (some go further and use int64 nanoseconds).

> **Try this now (after Stage 1 is up):**
> ```bash
> # Hit the API directly from the host (since we're inside compose, exec into tor):
> docker exec sentinelx-tor curl -s --socks5-hostname 127.0.0.1:9050 \
>   "http://$(cat tor_config/hidden_service/hostname)/api/posts?since=0&limit=2"
> ```
> Note the `--socks5-hostname` flag — that's curl's way of saying "send the hostname through the proxy, don't try to resolve `.onion` locally." This will come up huge in Stage 2.

A few smaller decisions in the API code:

- **`int()`/`float()` parsing wrapped in `try/except`.** If a client sends `?since=banana` we silently fall back to `0.0` instead of returning a 400 error. Could be more strict, but defaulting to permissive avoids dumb outages.
- **`limit` capped at 1000.** Without the cap, one HTTP request could drag the entire forum table. (You've seen the same idea on real APIs that paginate — usually it's `?limit=100` max.)

#### Template filters

```python
@app.template_filter("fmt_ts")
def fmt_ts(value):
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M UTC")
```

In a template you write `{{ thread.created_at | fmt_ts }}` and Jinja2 calls this function. Why bother? Because we store time as an epoch float (1777120704.17), but humans don't read floats. The filter converts it to "2026-04-27 12:38 UTC" right when rendering. Templates stay dumb — they just call the filter and don't have to understand timestamps.

### 3.2 The schema (`schema.sql`)

Two tables, three indexes, one cascade delete. Here's the whole thing distilled:

```sql
CREATE TABLE threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, category TEXT, author TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    author TEXT, body TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY(thread_id) REFERENCES threads(id) ON DELETE CASCADE
);
CREATE INDEX idx_posts_thread ON posts(thread_id);
CREATE INDEX idx_posts_created ON posts(created_at);
CREATE INDEX idx_threads_category ON threads(category);
```

Three things to know:

1. **`created_at REAL`** = SQLite's float type. Already covered above — numeric comparison is faster than parsing.

2. **Indexes on the three columns we filter on.** An *index* is a separate data structure (a B-tree) the database keeps so it can find rows by a column value without scanning every row in the table.

   > **Detour: why does an index matter at 235 posts?** It doesn't, much. But it matters at 235,000. The real lesson is: if you find yourself adding a `WHERE column = ?` query and there's no index on that column, the DB has to scan every row. Always think "is this column queried often? Should it be indexed?" Indexes cost disk space and slow inserts a tiny bit, but reads get massively faster.

   The classic pentest analogy: an index is like a phonebook sorted by name. Without it, finding "Smith" means flipping every page. With it, you jump straight to S.

3. **`FOREIGN KEY ... ON DELETE CASCADE`.** "Delete a thread, all its posts go with it." But this only takes effect because we set `PRAGMA foreign_keys = ON` in `app.py`. Without that, the rule is silently ignored.

### 3.3 The seeder (`seed_data.py`)

This file generates the fake forum content. The structure:

- **IOC pools:** lists like `FAKE_IPS = ["185.220.101.42", ...]`, `FAKE_CVES = ["CVE-2024-21413", ...]`, `FAKE_BTC = [...]`, plus `ACTORS` (threat actor names like "Lazarus Group"), `MALWARE` (names like "Cobalt Strike"), and `USERS` (plausible darknet handles like `gh0st`, `R00tk1t`).

- **Templates:** about 10 thread templates and 10 reply templates. Each template is a string with `{ip}`, `{cve}`, `{actor}` etc. placeholders. The seeder picks one and substitutes random values from the pools.

- **`random_post_body(category, ts)`** — picks a template that matches the category, fills it in, returns `(title, body)`.

- **`seed(conn, n_threads)`** — for each thread, picks a category and an author, picks a creation timestamp uniformly random within the last 14 days, writes the OP (original post), then writes 0–5 reply posts each strictly after the OP's time and capped at "now."

- **`--reset`** drops and recreates tables. Without it, the seeder appends to existing data.

- **`--seed 42`** sets the random number generator's seed so every fresh container generates *the same* fake forum. Reproducibility — important for debugging and demos.

> **Why realistic-looking IOCs?** Because Stage 3 is going to build a regex + spaCy extractor that pulls IPs, CVEs, hashes, BTC addresses, and named entities out of these post bodies. If our seed data was lorem ipsum, Stage 3 would have nothing to extract and we'd be testing extraction on data that doesn't represent reality. Real forum posts mix prose and IOCs in messy ways — the seeder mimics that.

> **One important defang detail.** Some seeded values use the bracket-defang convention you see in real CTI reports: `secure-update[.]xyz` instead of `secure-update.xyz`. The brackets prevent the link from being clickable, which is how analysts share IOCs without anyone accidentally hitting "phishing-domain.com" with a real browser. Stage 3 is going to have to *un-defang* (refang) these before regex matching.

### 3.4 The Tor `torrc`

This is Tor's config file. Five directives matter:

```
SOCKSPort 0.0.0.0:9050
SOCKSPolicy accept *
HiddenServiceDir /var/lib/tor/sentinelx_forum/
HiddenServiceVersion 3
HiddenServicePort 80 forum:5000
ClientOnly 1
```

What each does:

- **`SOCKSPort 0.0.0.0:9050`** — Tor will listen on port 9050 inside the container, on all interfaces (`0.0.0.0` means "any IP this container has"). We then publish that port from the container to the host as `127.0.0.1:9050` (in `docker-compose.yml`), so it's reachable from your machine but not from the LAN.

- **`SOCKSPolicy accept *`** — allow any client to use the proxy. **This is fine inside our container** because the only way to reach 9050 is through localhost, but on a public Tor instance this would be reckless — you'd be running an open SOCKS proxy that anyone on the internet could abuse to bounce traffic.

- **`HiddenServiceDir /var/lib/tor/sentinelx_forum/`** — where Tor stores your hidden-service identity. This directory will end up containing:
  - `hs_ed25519_secret_key` — the private key. **This file IS your `.onion` address.** Anyone who steals it can impersonate your hidden service on the network.
  - `hs_ed25519_public_key` — the public key.
  - `hostname` — a text file containing the `.onion` address (which is derived from the public key).

  We bind-mount this directory from the host, which is what makes the address survive `docker compose down -v`.

- **`HiddenServiceVersion 3`** — v3 onions (56-character addresses, ed25519 keys). v2 was deprecated in 2021 and removed from the network. Don't pick v2 — there's no choice, really.

- **`HiddenServicePort 80 forum:5000`** — when someone hits the `.onion` on port 80, Tor forwards the connection internally to `forum:5000`. The `forum` hostname is resolved by Docker's built-in DNS for the compose network — it's just the name of the other container.

- **`ClientOnly 1`** — we are not a Tor relay. Without this, our daemon could opportunistically participate in the relay network and forward other people's traffic. We're a CTI sandbox, not a Tor volunteer; this directive turns relay behaviour off.

### 3.5 The Tor entrypoint script (THE TRICKIEST FILE IN THE STAGE)

This is genuinely the most important pattern in this entire stage. Read it slowly:

```sh
#!/bin/sh
HS_DIR=/var/lib/tor/sentinelx_forum
mkdir -p "$HS_DIR"
chown -R debian-tor:debian-tor "$HS_DIR"
chmod 700 "$HS_DIR"
chown -R debian-tor:debian-tor /var/lib/tor

exec setpriv --reuid=debian-tor --regid=debian-tor --init-groups \
    tor -f /etc/tor/torrc
```

#### Why does this script exist at all?

Tor has a security rule: it will refuse to use a `HiddenServiceDir` unless that directory is owned by the same user Tor is running as, with permissions `0700` (only the owner can read/write/list). This is *correct* — the secret key inside is the cryptographic identity of your `.onion`. If anyone else can read it, they can impersonate you.

So Tor expects: the dir owned by `debian-tor`, mode 700.

#### The bind-mount problem

When you bind-mount a host directory into a container on **Docker Desktop (Windows or macOS)**, the directory comes into the container as `root:root`, **regardless of any `chown` you did at image build time**. This is a Docker Desktop quirk that bites everyone the first time. (On native Linux Docker it's different, but still has its own ownership issues.)

So Tor starts up, sees `/var/lib/tor/sentinelx_forum/` is owned by root not debian-tor, and dies with `is not owned by this user (debian-tor) but by root`.

#### The fix: start as root, fix permissions, drop privileges, then exec Tor

That's what those four `chown`/`chmod` lines do, and then `setpriv` is the magic.

- **`setpriv --reuid=debian-tor --regid=debian-tor --init-groups`** = "before running the next command, switch the running user to `debian-tor`." `--reuid` sets the real user ID, `--regid` sets the real group ID, `--init-groups` populates the supplementary group list as if logging in as that user.
- **`exec`** replaces the current shell with `tor`. This means `tor` becomes process ID 1 inside the container (PID 1 is "the boss process" of any Linux process tree).

Why `exec` matters: when you run `docker stop`, Docker sends `SIGTERM` to PID 1. If you forgot the `exec`, the *shell* would catch the signal, and `tor` would only get killed when the shell timed out and Docker escalated to `SIGKILL`. Slow shutdowns, possibly truncated state on disk.

> **Detour: why not just `USER debian-tor` in the Dockerfile?**
> `USER debian-tor` makes the container start as `debian-tor` from the very beginning. That's what you'd normally do for security. **But** if you start as `debian-tor`, you can't `chown` the bind mount on the way in — only root can `chown`. So the bind-mount comes in as root-owned, the `debian-tor` user can't access it, and Tor dies for a different reason. We have to start as root *just long enough* to fix the bind mount, then drop privileges. This pattern — **start as root, fix permissions, drop, exec the real binary** — is the standard answer to bind-mount ownership. Postgres does this, Redis does this, Elasticsearch does this. Memorize it.

> **The pentest framing:** privilege dropping is the same idea as a sudoer running `sudo` to do one privileged thing then going back to a regular user — minimize the time you're root. Containers that run their main service as root are a real privilege-escalation footprint; if there's a remote-code-exec in your Tor build, the attacker shouldn't get root just because you couldn't be bothered to drop privileges.

### 3.6 The Dockerfiles

#### Forum Dockerfile (the interesting one)

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

Things worth knowing:

- **`COPY requirements.txt .` BEFORE `COPY . .`**. Why? Docker images are built in **layers**, and each layer is cached. If you change `app.py`, only the layer that copies the source code is invalidated — the pip-install layer is reused from cache. If you'd done `COPY . .` first, every code edit would invalidate the pip layer and pip would re-run for 30 seconds on every rebuild. This pattern (copy dep manifest first → install → copy source) is universal in Dockerfiles.

- **`PYTHONUNBUFFERED=1`** — without this, Python's stdout is buffered and `print()` lines don't flush until the buffer fills. In Docker, that means logs lag. Annoying.

- **`PYTHONDONTWRITEBYTECODE=1`** — stops Python from creating `__pycache__/*.pyc` files inside the container. Just keeps the image cleaner.

- **The CMD is a shell command** because we have conditional logic: "if the DB file doesn't exist, run the seeder first, otherwise skip seeding." We also `exec gunicorn` at the end so gunicorn becomes PID 1 (same SIGTERM reason as Tor's entrypoint).

- **`gunicorn -w 2`** = run 2 worker processes. Why 2? Because we have one client (the scraper). The standard rule of thumb (`2 * cores + 1`) is for CPU-bound web apps under real load; we're a fixture.

#### Tor Dockerfile

Standard `debian:bookworm-slim` + `apt-get install tor ca-certificates util-linux`. We need `util-linux` for the `setpriv` binary. We don't add a `USER` directive — the entrypoint handles user dropping.

### 3.7 `docker-compose.yml`

The few non-obvious parts:

- **`expose: 5000` vs `ports: "127.0.0.1:9050:9050"`**:
  - `expose` only opens the port on the **internal compose network** — other compose containers can reach it, your host cannot. The forum uses this. The point is: the forum is *only* reachable through Tor.
  - `ports` actually publishes the port to the host. Tor uses this to expose its SOCKS5 to localhost.

- **`depends_on: condition: service_healthy`** — Tor doesn't start until the forum's healthcheck passes. Without `service_healthy`, `depends_on` only waits for the container *process* to exist, not for the app inside to actually be ready. That difference is why naive `depends_on` causes flaky compose stacks: tor publishes a hidden service, traffic comes in, but Flask hasn't bound port 5000 yet → 502 errors.

- **Volume vs bind mount, side by side:**
  - `forum_data` is a **named volume** — Docker manages it under `/var/lib/docker/volumes/...`. To wipe the forum DB and reseed, you have to run `docker compose down -v`. (The `-v` removes named volumes.)
  - `./tor_config/hidden_service` is a **bind mount** — the directory on your host *is* the directory inside the container. **It survives `docker compose down -v`** because compose doesn't touch host paths. This is intentional: we want the `.onion` address to outlive a `down -v`, so we don't have to re-paste it into the scraper config every time we wipe the forum data.

> **Try this:**
> ```bash
> docker compose down -v        # nukes forum_data, leaves .onion alone
> ls tor_config/hidden_service/  # still there
> docker compose up --build      # forum reseeds, .onion is the same
> ```
> If you ever want to *also* wipe the `.onion`, you have to manually `rm -rf tor_config/hidden_service/*`.

---

## 4. Why these choices over the alternatives

| Decision | Alternative | Why we picked this |
|----------|-------------|-------------------|
| Flask + SQLite | FastAPI + Postgres | Stage 1 is a fixture, not the main API. Flask + SQLite is one container, no separate DB process, no migration tooling. We spend the complexity budget on the Tor side, where the *real* learning is. FastAPI shows up in Stage 6 where async + auto-OpenAPI matter. |
| gunicorn (sync workers) | uvicorn / uWSGI | Flask is WSGI (sync). gunicorn is the obvious WSGI server. uvicorn is for ASGI (async) — overkill here, since Flask isn't async anyway. |
| v3 hidden service | v2 onions | v2 was removed from the Tor network in 2021. v3 is the only option in 2026. |
| Bind-mount the keypair | Regenerate per build | Stable `.onion` means scraper config doesn't churn. The keypair is gitignored so it never lands in the repo, but it persists locally so dev cycles don't keep changing the address. |
| `setpriv` to drop privileges | `gosu` / `su-exec` | `setpriv` is in `util-linux`, already in Debian, no extra binary to install. `gosu` works fine but is one more dependency. |
| Realistic IOCs in seed data | Lorem ipsum | Stages 3 + 4 need real-shaped signal. A forum that says "the quick brown fox" would teach us nothing about IOC extraction. |
| Two containers (forum + tor) | One container running both | Single-responsibility containers. `docker compose down` of one doesn't touch the other. The forum image stays clean Python; the Tor image stays clean Debian. Mixing them = one bloated image, confused logs. |
| Compose network DNS (`forum:5000`) | Hardcoded IPs | Docker's built-in DNS resolves service names. IPs change between `up` and `down`; service names don't. |
| `ClientOnly 1` | Default (relay-eligible) | We're a CTI lab, not a Tor volunteer. We don't want our laptop relaying anyone else's traffic. |
| Epoch-float timestamps | ISO-8601 strings | Numeric compare is faster, no timezone parsing. The Jinja `fmt_ts` filter handles human display. |

---

## 5. Tech-stack tour: where each piece lives in the real world

### 5.1 Flask

A WSGI microframework. Minimal surface — routes, request, jsonify, templates, that's most of it.

**In industry:** still extremely common for internal tools, admin dashboards, and **ML model-serving endpoints** (the original "load a pickled model and serve `/predict`" pattern — basically how every ML demo on GitHub looks). Has lost ground to FastAPI for new public APIs because async + automatic OpenAPI docs are big quality-of-life wins. Most large Python orgs (Pinterest, LinkedIn, parts of Netflix, much of Reddit's old stack) had Flask somewhere.

### 5.2 gunicorn

A pre-fork WSGI HTTP server. It spawns N worker processes, each running a copy of your WSGI app.

**In industry:** the default Python web app server. You'll see it behind nginx (a fast HTTP front-end) in basically every Python web deployment — Django, Flask, FastAPI in WSGI mode. The number-of-workers rule of thumb is `2 * cpu_cores + 1` for CPU-bound apps; we use 2 because we're a fixture.

> **Detour: WSGI vs ASGI?** WSGI is the older Python web standard — synchronous, one request per worker at a time. ASGI is the newer one — async, can multiplex many requests per worker. FastAPI is ASGI. We don't need async in Stage 1 because the forum has one client (the scraper) hitting it slowly.

### 5.3 SQLite

A serverless file-based SQL database. The whole DB is one file. Three rows of code to use it.

**In industry:** the most-deployed database in the world by install count. Every Android phone has it. Every iOS app has it. Every browser has it (Chrome's history, bookmarks, cookies — all SQLite). Every aircraft black box has it. It's production-appropriate for read-heavy and single-writer workloads up to roughly hundreds of GB. CTI tooling specifically: a lot of intel ingestion/exploration scripts use SQLite as a checkpoint store. **The whole SentinelX project will live happily in one SQLite file.**

### 5.4 Jinja2

A server-side templating engine. `{{ ... }}` for expressions, `{% ... %}` for control flow, autoescape on for HTML (which prevents the XSS class of vulns you've worked on — Jinja2 escapes user-supplied data by default unless you explicitly mark it `|safe`).

**In industry:** Flask, FastAPI's `Jinja2Templates`, **Ansible playbooks** (yes, the same Jinja2 — DevOps templating uses it everywhere), Salt states, dbt models (`{{ ref('foo') }}`), Home Assistant configs. If you know Jinja2 you can read half the YAML in DevOps land.

### 5.5 Tor (the daemon)

The reference implementation of the Tor protocol.

**What it actually does:**
1. Maintains an encrypted connection to three relays (a "circuit"). Each relay only knows the previous and next hop, not the full path.
2. Tunnels TCP through that circuit, so you can run any TCP-based application (HTTP, SSH, IRC) over it.
3. For hidden services, participates in the **rendezvous protocol** that lets a client reach a server without either side knowing the other's IP.

**In industry / research:** journalists' SecureDrop, whistleblowing platforms, dark web marketplaces (ours is a synthetic), and **every CTI vendor that monitors darknet sources** (Recorded Future, Flashpoint, Intel471, KELA — they all run Tor scrapers). The plumbing we set up here (SOCKS5 + `.onion` DNS resolution via the proxy) is exactly what those tools use, just at much larger scale.

### 5.6 v3 hidden services (the cryptography in one paragraph)

An onion address is `base32(public_ed25519_key + checksum + version_byte)`. 56 chars. **The address *is* the identity** — there's no certificate authority, no DNS, no central registrar. If you have the secret key file, you are that `.onion`. This is why `chmod 700` on the keypair dir matters and why our keypair is gitignored. Knowing this changes how you think about hidden-service security: protecting `hs_ed25519_secret_key` is the *whole game*.

**Industry:** Facebook (`facebookcorewwwi.onion` — yes, really, and they got a real EV cert for it), the New York Times, ProPublica, BBC News all run public hidden services for users in censored regions. The rendezvous protocol is *the* clever cryptographic primitive that makes server-side anonymity possible.

### 5.7 Docker + Docker Compose

Containers are Linux processes with their own namespaces (network, filesystem, process tree, user IDs) — they look like separate machines from the inside but they're just regular processes on the host kernel. Compose is the small orchestrator that runs a group of related containers.

**Industry:** containers eat the world. Every cloud-native app on Earth ships as a container image. Compose itself is for local dev and small single-host deployments — at scale, **Kubernetes** or ECS or Nomad takes over. But the *image* you build with Docker is the same artifact those orchestrators run. Learn Docker well and Kubernetes is "Docker Compose with networking superpowers."

### 5.8 Bind mounts vs named volumes

Both let you give a container persistent storage that survives container restarts. Differences:

| | Bind mount | Named volume |
|---|---|---|
| Location | A specific path on the host | Docker manages the storage internally |
| You can `cd` to it from host? | Yes | Sort of — buried under `/var/lib/docker/volumes/...` |
| Speed on Docker Desktop | Slower (crosses host FS boundary) | Faster (lives in the Linux VM) |
| Best for | Source code during dev, stable artifacts (our `.onion` keypair) | Databases (our `forum_data`) |

### 5.9 Privilege dropping in containers

The "container starts as root → fixes ownership / writes secrets → drops to non-root → execs the real process" pattern is **the** answer to bind-mount ownership traps. Real-world examples:

- Postgres official image: `docker-entrypoint.sh` → `gosu postgres postgres ...`
- Redis: same idea.
- Elasticsearch: same idea.

If you ever see `Permission denied` from inside a container that wasn't there before a bind mount got added — bet money it's this.

### 5.10 Healthchecks

Compose's `healthcheck` + `depends_on: condition: service_healthy` is how you say "don't start container B until container A's app is *actually ready* (not just running)."

**Industry:** in Kubernetes the equivalent is **readiness probes** + **startup probes**. In Nomad it's the `check` stanza. The shape is identical: don't route traffic to a thing until it tells you it's ready.

---

## 6. Industry context: how does Stage 1 map to a real CTI shop?

Real CTI vendors have a "darknet collection" team whose entire job is the equivalent of Stages 1–2:

1. **Source acquisition** — they curate hundreds-to-thousands of `.onion` URLs of interest (forums, marketplaces, leak sites, Telegram-mirror bridges).
2. **Crawler infrastructure** — distributed Tor exit pools (because real darknet sites rate-limit by circuit), captcha-solving fallbacks, account management for forums that require login, anti-bot evasion.
3. **Storage** — every page raw + parsed, deduplicated by content hash, indexed for full-text search.
4. **Pipeline** — the same NER/LLM/MITRE stages we are about to build, but at industrial scale.

Our Stage 1 is the **fixture** that lets us learn Stages 2–8 without doing any of the legal / ethical / operational hardening real darknet collection requires. The plumbing — SOCKS5 + `.onion` resolution + incremental polling — is identical to the real thing.

A useful test: when Stage 2's scraper points at our `.onion`, the only thing that changes for it to work against a real darknet forum is the URL list. *That's the contract this stage is honoring.*

---

## 7. Gotchas hit during the build

### 7.1 Bind-mount ownership on Docker Desktop
**Symptom:** Tor crash-loops with `is not owned by this user (debian-tor) but by root`.
**Cause:** Bind mounts on Docker Desktop come in as `root:root` regardless of image-time `chown`.
**Fix:** Runtime entrypoint that `chown`s + `setpriv`s. Already in `tor_config/entrypoint.sh`.
**Don't:** Revert to a `USER debian-tor` directive in the Dockerfile. Won't work with bind mount.

### 7.2 Transient network failures during apt/pip
**Symptom:** First `docker compose up --build` died mid-`apt-get install`.
**Fix:** Run it again. The base image was already cached, so the retry was cheap.
**Lesson:** Image builds are idempotent if you don't tag-and-discard between attempts. Don't `--no-cache` unless you have a reason.

### 7.3 `Socks version 71 not recognized` warnings
**Symptom:** Spam in tor logs.
**Cause:** Some local process on your machine is probing port 9050 like it's an HTTP proxy. Tor's SOCKS handler correctly rejects.
**Fix:** Ignore. (If our actual scraper gets the same error, *then* care.)

### 7.4 OneDrive + Docker
The repo lives under a OneDrive-synced folder. OneDrive can lock files mid-write. If a Docker build fails with weird I/O errors, pause OneDrive sync and retry. Don't move the repo without checking with the user.

### 7.5 Forum DB persistence vs `.onion` persistence are decoupled on purpose
- `forum_data` (named volume) → wiping it requires `docker compose down -v`.
- `tor_config/hidden_service/` (bind mount) → survives `down -v`. The `.onion` address is stable.

If you find yourself wanting to wipe the `.onion` and start fresh, manually `rm -rf tor_config/hidden_service/*`. Don't rely on `down -v` to do it.

---

## 8. What Stage 2 looks like (so you can see why Stage 1 was shaped this way)

Stage 2 builds the **scraper**:

- Reads the `.onion` from `tor_config/hidden_service/hostname`.
- Uses `httpx` with a SOCKS5 proxy at `127.0.0.1:9050` so its HTTP requests get routed through Tor.
- Polls `GET /api/posts?since=<last_seen>` on a loop, dedups by post id, writes new rows to a `raw_posts` table.
- Surfaces a CLI: `python -m backend.scraper.run --once`, `--watch`, `--reset-cursor`.

**Notice how Stage 1 makes Stage 2 trivial.** The scraper just hits a URL through SOCKS5; everything else (the corpus, the pagination cursor, the realistic content) was set up here. **That's the payoff for building the fixture properly.**

---

## 9. The five things to actually remember

1. **`.onion` resolution must happen at the proxy, not on your local resolver.** Your DNS doesn't know `.onion`; only Tor does. (Stage 2 will dive into the `socks5://` vs `socks5h://` rabbit hole that follows from this.)

2. **The hidden-service keypair is your identity.** Mode 700, owned by the running user, never in git, bind-mounted for persistence. Steal the keypair = impersonate the `.onion`.

3. **Bind mounts on Docker Desktop come in as `root:root` at runtime.** Fix it in the entrypoint, drop privileges with `setpriv`, exec the real process. Memorize this pattern.

4. **`depends_on: condition: service_healthy`** is the only `depends_on` worth using. Plain `depends_on` only waits for the container to start — not for the app inside to be ready. That's how flaky compose stacks happen.

5. **Stage 1 is a fixture for the entire pipeline.** Every later stage assumes a stable `.onion`, an incremental-poll JSON API, and IOC-rich content. None of those choices were arbitrary — they're the contract Stages 2–8 will lean on.

---

**End of Stage 1 LEARN.** When you've finished reading, the same code under `onion_service/` and `tor_config/` should look about 50% less alien. If something still feels confusing, that's a flag to me — leave a note for the next session.
