"""
discovery/seen_items.py -- zero unit test coverage existed before this
file. Covers the batched store.batch() rewrite (2026-07-17, real 45-minute
Sunday timeout investigation): filter_unseen/mark_seen now issue ONE
store.batch() call covering every item instead of one store.get()/
store.put() per item (measured 12.1x faster on a real 10-key benchmark
against the live store). Confirms correctness of the batched rewrite,
not just that it runs.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from langgraph.store.base import GetOp, PutOp
import discovery.seen_items as seen_items_mod
from discovery.seen_items import filter_unseen, mark_seen


class _FakeStore:
    def __init__(self, seen_urls=None):
        self._seen = set(seen_urls or [])
        self.batch_calls: list[list] = []

    def batch(self, ops):
        self.batch_calls.append(list(ops))
        results = []
        for op in ops:
            if isinstance(op, GetOp):
                results.append({"seen": True} if op.key in self._seen else None)
            elif isinstance(op, PutOp):
                self._seen.add(op.key)
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
