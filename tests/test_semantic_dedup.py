import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from discovery.semantic_dedup import dedupe_semantic, _NAMESPACE


class _Item:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _FakeStore:
    def __init__(self, seed: dict | None = None):
        self._data = dict(seed or {})
        self.deleted: list[str] = []
        self.puts: list[tuple] = []

    def search(self, namespace, limit=1000):
        return [_Item(k, v) for k, v in self._data.items()][:limit]

    def delete(self, namespace, key):
        self.deleted.append(key)
        self._data.pop(key, None)

    def put(self, namespace, key, value):
        self._data[key] = value
        self.puts.append((namespace, key, value))


def _item(url, title, text, fetched_at="2026-07-16T00:00:00+00:00"):
    return {"url": url, "title": title, "text": text, "author_name": "", "author_handle": "",
            "fetched_at": fetched_at, "is_thread": False, "thread_contents": None,
            "expanded_urls": [], "source": "blog_scrape", "duplicate_count": 1}


def _embed_side_effect(vectors_by_text):
    def _fn(text):
        for key, vec in vectors_by_text.items():
            if key in text:
                return vec, 10
        raise KeyError(f"no fixture vector for text containing: {text[:50]}")
    return _fn


def test_within_run_near_duplicates_keep_earliest_published():
    """Tie-break is earliest fetched_at, not fuller text (verbosity isn't
    quality; the earlier item is more likely the original reporting) --
    per batch2-dedup-taste-spec.md Section 4's revised tie-breaker."""
    earlier = _item("https://a.com/1", "Story A", "short version", fetched_at="2026-07-16T08:00:00+00:00")
    later_but_fuller = _item("https://b.com/1", "Story A elsewhere", "much longer, fuller version of the same story", fetched_at="2026-07-16T14:00:00+00:00")

    fake_store = _FakeStore()
    vectors = {"short version": [1.0, 0.0], "fuller version": [0.999, 0.001]}  # cosine ~1.0, above 0.90

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_text", side_effect=_embed_side_effect(vectors)):
        survivors, costs = dedupe_semantic([earlier, later_but_fuller], run_id="run-1")

    assert len(survivors) == 1
    assert survivors[0]["url"] == "https://a.com/1"  # earlier-published (first-seen) item won, despite shorter text
    drop_errors = [c["error"] for c in costs if c.get("error")]
    assert any("dropped as duplicate of" in e and "a.com" in e for e in drop_errors)


def test_within_run_duplicate_arriving_earlier_in_list_but_published_later_gets_swapped_out():
    """The opposite ordering -- first-seen item is actually published
    LATER -- confirms the swap direction is driven by fetched_at, not by
    which item happened to be processed first."""
    later = _item("https://a.com/1", "Story A", "processed first", fetched_at="2026-07-16T14:00:00+00:00")
    earlier = _item("https://b.com/1", "Story A elsewhere", "processed second", fetched_at="2026-07-16T08:00:00+00:00")

    fake_store = _FakeStore()
    vectors = {"processed first": [1.0, 0.0], "processed second": [0.999, 0.001]}

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_text", side_effect=_embed_side_effect(vectors)):
        survivors, costs = dedupe_semantic([later, earlier], run_id="run-1")

    assert len(survivors) == 1
    assert survivors[0]["url"] == "https://b.com/1"  # swapped in despite arriving second in the list


def test_items_below_threshold_both_survive():
    a = _item("https://a.com/1", "Story A", "text about agents")
    b = _item("https://b.com/1", "Story B", "text about databases")

    fake_store = _FakeStore()
    vectors = {"about agents": [1.0, 0.0], "about databases": [0.0, 1.0]}  # orthogonal, cosine 0.0

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_text", side_effect=_embed_side_effect(vectors)):
        survivors, costs = dedupe_semantic([a, b], run_id="run-1")

    assert len(survivors) == 2
    assert not any(c.get("error") for c in costs)


def test_cross_run_match_drops_new_item_unconditionally():
    new_item = _item("https://new.com/1", "Same story again", "a repeat of an old story")
    old_scored_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    fake_store = _FakeStore(seed={
        "https://old.com/1": {
            "item_id": "https://old.com/1", "url": "https://old.com/1",
            "embedding_vector": [1.0, 0.0], "scored_at": old_scored_at,
        },
    })
    vectors = {"repeat of an old story": [0.9999, 0.0001]}

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_text", side_effect=_embed_side_effect(vectors)):
        survivors, costs = dedupe_semantic([new_item], run_id="run-1")

    assert survivors == []
    assert any("previously-seen" in c["error"] and "old.com" in c["error"] for c in costs)
    # new item's own embedding must NOT be persisted since it was dropped
    assert "https://new.com/1" not in fake_store._data


def test_window_entries_older_than_7_days_are_excluded_and_deleted():
    item = _item("https://a.com/1", "Fresh story", "genuinely new content")
    stale_scored_at = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    fake_store = _FakeStore(seed={
        "https://stale.com/1": {
            "item_id": "https://stale.com/1", "url": "https://stale.com/1",
            "embedding_vector": [1.0, 0.0], "scored_at": stale_scored_at,
        },
    })
    # vector identical to the stale entry's -- would match if window filtering were broken
    vectors = {"genuinely new content": [1.0, 0.0]}

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_text", side_effect=_embed_side_effect(vectors)):
        survivors, costs = dedupe_semantic([item], run_id="run-1")

    assert len(survivors) == 1  # stale entry excluded from comparison, so no match -> survives
    assert "https://stale.com/1" in fake_store.deleted  # lazily cleaned up


def test_failed_embed_call_passes_item_through_unfiltered():
    item = _item("https://a.com/1", "Story A", "some text")
    fake_store = _FakeStore()

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_text", side_effect=RuntimeError("API down")):
        survivors, costs = dedupe_semantic([item], run_id="run-1")

    assert survivors == [item]
    assert any("embed failed" in c["error"] and "passed through unfiltered" in c["error"] for c in costs)
    assert fake_store.puts == []  # nothing to persist, no vector was produced
