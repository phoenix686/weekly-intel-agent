"""
prefilter-drops-audit-logging (batch2-dedup-taste-spec.md Section 8):
every dedup or taste-filter drop logged to ("weekly_intel","prefilter_drops")
with two separate optional fields -- a dedup drop populates
compared_against_item_id and leaves compared_against_tag null; a taste
drop does the reverse. No entry should ever have both set or both null.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from langgraph.store.base import GetOp, PutOp

from discovery.semantic_dedup import dedupe_semantic, _DROPS_NAMESPACE as _DEDUP_DROPS_NAMESPACE
from discovery.taste_vectors import taste_prefilter, _DROPS_NAMESPACE as _TASTE_DROPS_NAMESPACE


class _Item:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _FakeStore:
    def __init__(self, seed: dict | None = None):
        self._data = dict(seed or {})
        self.puts: list[tuple] = []

    def search(self, namespace, limit=1000):
        return [_Item(k, v) for k, v in self._data.items()][:limit]

    def delete(self, namespace, key):
        self._data.pop(key, None)

    def put(self, namespace, key, value):
        self._data[key] = value
        self.puts.append((namespace, key, value))

    def batch(self, ops):
        """Real PostgresStore.batch() dispatches a list of Get/PutOp in
        one call -- mirrored here via the existing put()/get() so
        self.puts still records every write, real batching or not."""
        results = []
        for op in ops:
            if isinstance(op, PutOp):
                self.put(op.namespace, op.key, op.value)
                results.append(None)
            elif isinstance(op, GetOp):
                results.append(self._data.get(op.key))
        return results


def _dedup_item(url, title, text):
    return {"url": url, "title": title, "text": text, "author_name": "", "author_handle": "",
            "fetched_at": "2026-07-16T00:00:00+00:00", "is_thread": False, "thread_contents": None,
            "expanded_urls": [], "source": "blog_scrape", "duplicate_count": 1}


def test_dedup_drop_logs_item_id_field_not_tag_field():
    a = _dedup_item("https://a.com/1", "Story", "short")
    b = _dedup_item("https://b.com/1", "Story", "same story elsewhere")
    fake_store = _FakeStore()
    vectors = {"short": [1.0, 0.0], "same story elsewhere": [0.999, 0.001]}

    def _embed_texts_side_effect(texts):
        result = []
        for text in texts:
            for key, vec in vectors.items():
                if key in text:
                    result.append(vec)
                    break
            else:
                raise KeyError(text)
        return result, [10] * len(texts)

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_texts", side_effect=_embed_texts_side_effect):
        dedupe_semantic([a, b], run_id="run-1")

    drops = [p for p in fake_store.puts if p[0] == _DEDUP_DROPS_NAMESPACE]
    assert len(drops) == 1
    entry = drops[0][2]
    assert entry["filter_type"] == "dedup"
    assert entry["compared_against_item_id"] is not None
    assert entry["compared_against_tag"] is None
    assert entry["run_id"] == "run-1"
    assert isinstance(entry["similarity_score"], float)


def test_taste_drop_logs_tag_field_not_item_id_field():
    item = _dedup_item("https://a.com/1", "Unrelated", "nothing on topic")
    topic_vectors_seed = {"agentic-engineering": {"tag": "agentic-engineering", "embedding_vector": [1.0, 0.0]}}
    fake_store = _FakeStore(seed=topic_vectors_seed)

    with patch("discovery.taste_vectors.get_store", return_value=fake_store), \
         patch("discovery.taste_vectors.embed_texts", return_value=([[0.0, 1.0]], [10])):
        taste_prefilter([item], run_id="run-2")

    drops = [p for p in fake_store.puts if p[0] == _TASTE_DROPS_NAMESPACE]
    assert len(drops) == 1
    entry = drops[0][2]
    assert entry["filter_type"] == "taste"
    assert entry["compared_against_tag"] == "agentic-engineering"
    assert entry["compared_against_item_id"] is None
    assert entry["run_id"] == "run-2"


def test_no_drop_entry_ever_has_both_fields_set_or_both_null():
    a = _dedup_item("https://a.com/1", "Story", "short")
    b = _dedup_item("https://b.com/1", "Story", "same story elsewhere")
    fake_store = _FakeStore()
    vectors = {"short": [1.0, 0.0], "same story elsewhere": [0.999, 0.001]}

    def _embed_texts_side_effect(texts):
        result = []
        for text in texts:
            for key, vec in vectors.items():
                if key in text:
                    result.append(vec)
                    break
            else:
                raise KeyError(text)
        return result, [10] * len(texts)

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_texts", side_effect=_embed_texts_side_effect):
        dedupe_semantic([a, b], run_id="run-3")

    drop_entries = [entry for namespace, _, entry in fake_store.puts if namespace == _DEDUP_DROPS_NAMESPACE]
    assert drop_entries  # sanity: the fixture actually produced at least one drop
    for entry in drop_entries:
        both_set = entry["compared_against_item_id"] is not None and entry["compared_against_tag"] is not None
        both_null = entry["compared_against_item_id"] is None and entry["compared_against_tag"] is None
        assert not both_set
        assert not both_null
