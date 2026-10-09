"""Watchlists: backfill is history (seen), new posts raise unseen alerts, idempotent."""

import pytest

from backend.api import watch
from backend.db.store import Store


# Helper: build a fake post dict
def post(pid, title, body):
    return {"id": pid, "thread_id": pid, "thread_title": title, "category": "c",
            "author": "a", "body": body, "created_at": float(pid)}


def test_watchlist_backfill_then_new_alert(tmp_path):
    s = Store(tmp_path / "w.db")
    s.insert_posts([post(1, "Selling ACME Corp VPN", "access to acme corp network"),
                    post(2, "Unrelated", "nothing here")])
    wl = watch.create(s.conn, "ACME", ["ACME Corp", "acme-vpn.online"])
    assert (wl["hits"], wl["unseen"]) == (1, 0)          # history shown, not alerted

    s.insert_posts([post(3, "Dump", "fresh combo list incl. acme corp staff")])
    assert watch.check_all(s.conn) == 1
    assert watch.check_all(s.conn) == 0                  # incremental + idempotent
    unseen = [a for a in watch.alerts(s.conn) if not a["seen"]]
    assert [a["raw_post_id"] for a in unseen] == [3]
    assert unseen[0]["matched_terms"] == ["ACME Corp"]

    watch.mark_seen(s.conn, None)
    assert watch.get(s.conn, wl["id"])["unseen"] == 0


def test_terms_are_cleaned_and_required(tmp_path):
    s = Store(tmp_path / "w2.db")
    wl = watch.create(s.conn, "", ["  okta  ", "OKTA", "x"])
    assert wl["terms"] == ["okta"] and wl["name"] == "okta"
    with pytest.raises(ValueError):
        watch.create(s.conn, "bad", ["x"])
