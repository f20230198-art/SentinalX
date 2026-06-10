"""HTTP client for the synthetic darknet forum, routed through Tor SOCKS5.

DNS-through-proxy note:
    For .onion addresses your local DNS cannot resolve the hostname — only Tor
    can. Many SOCKS clients use the 'socks5h://' scheme to opt into proxy-side
    DNS. httpx (with httpx[socks] / socksio) does NOT recognize 'socks5h://'
    as a scheme, but its SOCKS5 transport already resolves hostnames remotely
    by default, so a plain 'socks5://' URL behaves the way 'socks5h://' would
    in curl/requests-land. Don't change this back to 'socks5h://' — httpx
    will raise "Unknown scheme for proxy URL".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

DEFAULT_SOCKS_PROXY = "socks5://127.0.0.1:9050"
DEFAULT_HOSTNAME_FILE = Path(__file__).resolve().parents[2] / "tor_config" / "hidden_service" / "hostname"

# Tor circuits are slow. Generous timeouts.
DEFAULT_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=60.0)


def read_onion_hostname(path: Path = DEFAULT_HOSTNAME_FILE) -> str:
    if not path.exists():
        raise FileNotFoundError(
            f"Tor hidden-service hostname file not found at {path}. "
            "Is the Tor + .onion forum stack running? `docker compose up --build`."
        )
    addr = path.read_text(encoding="utf-8").strip()
    if not addr.endswith(".onion"):
        raise ValueError(f"hostname file did not contain a .onion address: {addr!r}")
    return addr


class ForumClient:
    def __init__(
        self,
        onion: str,
        proxy: str = DEFAULT_SOCKS_PROXY,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = f"http://{onion}"
        self._client = httpx.Client(proxy=proxy, timeout=timeout, follow_redirects=False)

    def __enter__(self) -> "ForumClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def fetch_posts(
        self,
        since: float,
        limit: int = 1000,
        category: str | None = None,
    ) -> dict:
        params: dict[str, Any] = {"since": since, "limit": limit}
        if category:
            params["category"] = category
        r = self._client.get(f"{self.base_url}/api/posts", params=params)
        r.raise_for_status()
        return r.json()
