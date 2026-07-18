"""
discovery/seen_items.py -- zero unit test coverage existed before this
file. Covers the batched store.batch() rewrite (2026-07-17, real 45-minute
Sunday timeout investigation): filter_unseen/mark_seen now issue ONE
store.batch() call covering every item instead of one store.get()/
store.put() per item (measured 12.1x faster on a real 10-key benchmark
against the live store). Confirms correctness of the batched rewrite,
not just that it runs.

Also covers the rolling 35-day expiry added 2026-07-18: mark_seen() now
writes a seen_at timestamp, and filter_unseen() runs a lazy sweep
(_expire_stale_entries) that deletes anything past the window in one
batched call -- store.delete() is itself just PutOp(namespace, key, None)
under the hood (langgraph.store.base), mirrored here via the same
_FakeStore.batch() dispatch.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from langgraph.store.base import GetOp, PutOp
import discovery.seen_items as seen_items_mod
from discovery.seen_items import filter_unseen, mark_seen


class _Item:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _FakeStore:
    def __init__(self, seen_urls=None, seed: dict | None = None):
        self._data: dict = dict(seed or {})
        for url in (seen_urls or []):
            self._data.setdefault(url, {"seen": True})
        self.batch_calls: list[list] = []

    def search(self, namespace, limit=10000):
        return [_Item(k, v) for k, v in self._data.items()][:limit]

    def batch(self, ops):
        self.batch_calls.append(list(ops))
        results = []
        for op in ops:
            if isinstance(op, GetOp):
                results.append(self._data.get(op.key))
            elif isinstance(op, PutOp):
                if op.value is None:
                    self._data.pop(op.key, None)
                else:
                    self._data[op.key] = op.value
                results.append(None)
        return results


def _item(url):
    return {"url": url, "title": "T", "text": "t"}


def test_filter_unseen_splits_correctly_via_one_batch_call():
    fake_store = _FakeStore(seen_urls={"https://a.com/1", "https://c.com/1"})
    items = [_item("https://a.com/1"), _item("https://b.com/1"), _item("https://c.com/1")]

    with patch.object(seen_items_mod, "get_store", return_value=fake_store):
        unseen, seen_urls = filter_unseen(items)

    assert len(fake_store.batch_calls) == 1  # one batched call, not three individual ones
    assert len(fake_store.batch_calls[0]) == 3  # covering all three items
    assert [i["url"] for i in unseen] == ["https://b.com/1"]
    assert sorted(seen_urls) == ["https://a.com/1", "https://c.com/1"]


def test_filter_unseen_empty_list_makes_no_store_call():
    fake_store = _FakeStore()
    with patch.object(seen_items_mod, "get_store", return_value=fake_store):
        unseen, seen_urls = filter_unseen([])

    assert unseen == []
    assert seen_urls == []
    assert fake_store.batch_calls == []


def test_mark_seen_issues_one_batch_call_covering_every_url():
    fake_store = _FakeStore()

    with patch.object(seen_items_mod, "get_store", return_value=fake_store):
        mark_seen(["https://a.com/1", "https://b.com/1"])

    assert len(fake_store.batch_calls) == 1
    put_ops = fake_store.batch_calls[0]
    assert len(put_ops) == 2
    assert all(isinstance(op, PutOp) for op in put_ops)
    assert {op.key for op in put_ops} == {"https://a.com/1", "https://b.com/1"}
    # seen_at is now written alongside seen -- the expiry window's basis
    assert all("seen_at" in op.value for op in put_ops)

    # confirms it round-trips: a subsequent filter_unseen sees them as seen now
    with patch.object(seen_items_mod, "get_store", return_value=fake_store):
        unseen, seen_urls = filter_unseen([_item("https://a.com/1"), _item("https://b.com/1")])
    assert unseen == []
    assert sorted(seen_urls) == ["https://a.com/1", "https://b.com/1"]


def test_mark_seen_empty_list_makes_no_store_call():
    fake_store = _FakeStore()
    with patch.object(seen_items_mod, "get_store", return_value=fake_store):
        mark_seen([])
    assert fake_store.batch_calls == []


def test_filter_unseen_expires_entries_past_the_35_day_window():
    stale_at = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    fresh_at = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    fake_store = _FakeStore(seed={
        "https://stale.com/1": {"seen": True, "seen_at": stale_at},
        "https://fresh.com/1": {"seen": True, "seen_at": fresh_at},
    })

    with patch.object(seen_items_mod, "get_store", return_value=fake_store):
        unseen, seen_urls = filter_unseen([_item("https://stale.com/1"), _item("https://fresh.com/1")])

    # the stale entry was deleted by the expiry sweep before the GetOp lookup
    # ran, so it now looks unseen (correctly re-fetchable); the fresh one is
    # still tracked as seen.
    assert [i["url"] for i in unseen] == ["https://stale.com/1"]
    assert seen_urls == ["https://fresh.com/1"]
    assert "https://stale.com/1" not in fake_store._data


def test_filter_unseen_does_not_expire_entries_with_no_seen_at():
    """Pre-migration entries (written before 2026-07-18) have no seen_at at
    all -- treated as NOT yet eligible for expiry, not as already-expired,
    since there's no real signal for their true age. Deleting real
    cross-run dedup history on a guess would repeat a mistake already
    flagged once tonight."""
    fake_store = _FakeStore(seed={"https://legacy.com/1": {"seen": True}})

    with patch.object(seen_items_mod, "get_store", return_value=fake_store):
        unseen, seen_urls = filter_unseen([_item("https://legacy.com/1")])

    assert unseen == []
    assert seen_urls == ["https://legacy.com/1"]
    assert "https://legacy.com/1" in fake_store._data
