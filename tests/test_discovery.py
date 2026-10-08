"""Discovery: generic page reader, result parsing, local index, engine failure."""

from backend.db.store import Store
from backend.discovery import search as search_mod
from backend.discovery.engines import EngineSpec, _fts_query, parse_results
from backend.discovery.page_reader import normalise_url, read_page, url_id

ONION = "a" * 56 + ".onion"
OTHER = "b" * 56 + ".onion"


def test_page_reader_keeps_content_drops_chrome():
    html = f"""<html><head><title>Fresh RDP access</title><script>evil()</script></head>
    <body><nav>Home | Login</nav><main><h1>Fresh RDP access</h1>
    <p>Selling domain admin on a US hospital network, 4k hosts, price 0.5 BTC.</p>
    <p>Contact on session. Escrow accepted.</p></main><footer>© market</footer></body></html>"""
    post = read_page(html, f"http://{ONION}/listing/7")
    assert post["thread_title"] == "Fresh RDP access"
    assert "domain admin" in post["body"] and "evil()" not in post["body"]
    assert "Home | Login" not in post["body"] and "© market" not in post["body"]
    assert post["source"] == ONION and post["category"] == "discovered"


def test_page_reader_rejects_empty_pages():
    assert read_page("<html><body><p>hi</p></body></html>", f"http://{ONION}/") is None


def test_url_identity_is_stable_and_normalised():
    assert url_id(f"http://{ONION.upper()}/a/") == url_id(f"{ONION}/a#frag")
    assert normalise_url(f"{ONION}/x/") == f"http://{ONION}/x"


def test_result_parser_finds_direct_and_redirected_onion_links():
    html = f"""<ol>
      <li><a href="http://{ONION}/thread/1">Leaked VPN creds</a> <p>vpn dump fresh</p></li>
      <li><a href="/search/redirect?redirect_url=http%3A%2F%2F{OTHER}%2F">Ransom blog</a></li>
      <li><a href="https://clearnet.example.com/">not onion</a></li>
      <li><a href="http://{ONION}/thread/1">duplicate</a></li>
    </ol>"""
    rs = parse_results(html, "test")
    assert [r.onion for r in rs] == [ONION, OTHER]
    assert rs[0].title == "Leaked VPN creds" and "vpn dump" in rs[0].snippet


def test_fts_query_neutralises_operators():
    q = _fts_query('vpn" OR NEAR(x) AND * "unclosed')
    # every term is quoted, so FTS syntax in user input can't break the query
    assert q.count('"') % 2 == 0 and "NEAR(" not in q.replace('"near"', "")


def test_local_search_and_failing_engine(tmp_path, monkeypatch):
    s = Store(tmp_path / "d.db")
    s.insert_posts([{"id": 1, "thread_id": 1, "thread_title": "WTS: VPN creds", "category": "access",
                     "author": "x", "body": "working vpn credentials for a fintech", "created_at": 1.0}])
    s.insert_posts([{"id": 2, "thread_id": 2, "thread_title": "Cooking", "category": "general",
                     "author": "y", "body": "nothing relevant here at all", "created_at": 2.0}])

    class Boom:
        def __init__(self, spec):
            pass

        def search(self, q, limit):
            raise ConnectionError("SOCKS proxy down")

    monkeypatch.setattr(search_mod, "OnionSearchEngine", Boom)
    monkeypatch.setattr(search_mod, "ENGINES", [EngineSpec("fake", "Fake", "http://x/{q}")])
    out = search_mod.run_search(s.conn, "vpn credentials", engines=["local", "fake"])
    assert [r["post_id"] for r in out["results"]] == [1]
    assert "Tor proxy not reachable" in out["errors"]["fake"]   # engine failed, search didn't


def test_fts_index_follows_new_inserts(tmp_path):
    s = Store(tmp_path / "e.db")
    s.insert_posts([{"id": 9, "thread_id": 9, "thread_title": "zeroday broker", "category": "c",
                     "author": "a", "body": "selling a chrome zeroday", "created_at": 1.0}])
    out = search_mod.run_search(s.conn, "zeroday", engines=["local"])
    assert out["results"][0]["title"] == "zeroday broker"
