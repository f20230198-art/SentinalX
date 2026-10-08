"""API smoke tests against a throwaway copy of the committed demo DB."""

import importlib
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DEMO_DB = Path(__file__).resolve().parents[1] / "backend" / "db" / "sentinelx.db"


def make_client(tmp_path, monkeypatch, **env) -> TestClient:
    db = tmp_path / "api.db"
    shutil.copy(DEMO_DB, db)
    monkeypatch.setenv("SENTINELX_DB_PATH", str(db))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import backend.api.main as main
    main = importlib.reload(main)   # config is read at import time
    return TestClient(main.app)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("SENTINELX_API_KEY", raising=False)
    with make_client(tmp_path, monkeypatch) as c:
        yield c


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_posts_list_and_detail(client):
    page = client.get("/posts", params={"limit": 5}).json()
    assert page["total"] > 0 and len(page["items"]) == 5
    pid = page["items"][0]["id"]
    detail = client.get(f"/posts/{pid}").json()
    assert detail["post"]["id"] == pid
    assert {"iocs", "entities", "techniques", "mitigations"} <= detail.keys()


def test_unknown_post_is_404(client):
    assert client.get("/posts/999999999").status_code == 404


def test_writes_need_key_when_configured(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, SENTINELX_API_KEY="s3cret") as c:
        body = {"name": "x", "filters": {}}
        assert c.post("/investigations", json=body).status_code == 401
        assert c.post("/investigations", json=body,
                      headers={"x-api-key": "s3cret"}).status_code == 201
        assert c.get("/posts").status_code == 200   # reads stay open


def test_expensive_endpoints_are_rate_limited(tmp_path, monkeypatch):
    monkeypatch.delenv("SENTINELX_API_KEY", raising=False)
    with make_client(tmp_path, monkeypatch, SENTINELX_RATE_LIMIT="2") as c:
        # Not a .onion -> 400, but each call still counts against the limit.
        codes = [c.post("/scrape-jobs", json={"onion_url": "example.com"}).status_code
                 for _ in range(3)]
        assert codes == [400, 400, 429]


def test_page_jobs_only_accept_onion_urls(client):
    r = client.post("/scrape-jobs", json={"urls": ["https://example.com/page"]})
    assert r.status_code == 400 and ".onion" in r.json()["detail"]


def test_discover_search_local(client):
    r = client.post("/discover/search", json={"query": "vpn credentials", "engines": ["local"]})
    assert r.status_code == 200
    body = r.json()
    assert body["results"] and body["results"][0]["engine"] == "local"
