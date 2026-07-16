import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from discovery.taste_vectors import taste_prefilter, recompute_topic_vectors, TOPIC_TAGS, _TAG_TO_BULLET


class _Item:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _FakeStore:
    def __init__(self, seed: dict | None = None):
        self._data = dict(seed or {})

    def search(self, namespace, limit=100):
        return [_Item(k, v) for k, v in self._data.items()][:limit]

    def put(self, namespace, key, value):
        self._data[key] = value


def _item(url, title, text):
    return {"url": url, "title": title, "text": text, "author_name": "", "author_handle": "",
            "fetched_at": "2026-07-16T00:00:00+00:00", "is_thread": False, "thread_contents": None,
            "expanded_urls": [], "source": "blog_scrape", "duplicate_count": 1}


def _topic_vectors_seed():
    # 20-dim one-hot vectors (dims 0-5) -- real embeddings are
    # high-dimensional, so an item vector CAN sit near-orthogonal to every
    # topic vector at once. A 6-dim one-hot basis can't model that: any
    # 6-dim unit vector's max cosine similarity against 6 orthonormal
    # one-hot vectors is mathematically >= 1/sqrt(6) =~ 0.408, always
    # above the 0.30 threshold -- that would make a genuine "below
    # threshold for every topic" case impossible to construct here.
    def _one_hot(dim):
        v = [0.0] * 20
        v[dim] = 1.0
        return v
    tags = ["agentic-engineering", "memory-systems", "llm-tooling", "evals", "learning-resource", "distributed-systems"]
    return {tag: {"tag": tag, "embedding_vector": _one_hot(i)} for i, tag in enumerate(tags)}


# A dimension no topic vector touches -- exactly orthogonal to all 6, cosine 0.0 against every one.
_IRRELEVANT_VECTOR = [0.0] * 19 + [1.0]


def test_item_matching_one_topic_strongly_survives_via_max_not_average():
    """Strong match on agentic-engineering, zero on everything else --
    averaging would drag this well below 0.30, max should not."""
    item = _item("https://a.com/1", "Agent harness design", "deep agentic-engineering content")
    fake_store = _FakeStore(seed=_topic_vectors_seed())

    strong_agentic_vector = [0.95] + [0.0] * 19  # dim 0 = agentic-engineering
    with patch("discovery.taste_vectors.get_store", return_value=fake_store), \
         patch("discovery.taste_vectors.embed_text", return_value=(strong_agentic_vector, 10)):
        survivors, costs = taste_prefilter([item], run_id="run-1")

    assert survivors == [item]
    assert not any(c.get("error") for c in costs)


def test_item_below_threshold_against_every_topic_is_dropped():
    item = _item("https://a.com/1", "Unrelated item", "nothing to do with any topic")
    fake_store = _FakeStore(seed=_topic_vectors_seed())

    with patch("discovery.taste_vectors.get_store", return_value=fake_store), \
         patch("discovery.taste_vectors.embed_text", return_value=(_IRRELEVANT_VECTOR, 10)):
        survivors, costs = taste_prefilter([item], run_id="run-1")

    assert survivors == []
    assert any("dropped by taste pre-filter" in c["error"] for c in costs)


def test_empty_topic_vector_store_lets_everything_through():
    item = _item("https://a.com/1", "Anything", "no topic vectors exist yet")
    fake_store = _FakeStore(seed={})

    with patch("discovery.taste_vectors.get_store", return_value=fake_store), \
         patch("discovery.taste_vectors.embed_text") as mock_embed:
        survivors, costs = taste_prefilter([item], run_id="run-1")

    assert survivors == [item]
    assert costs == []
    mock_embed.assert_not_called()  # never even tries to embed if there's nothing to compare against


def test_failed_embed_call_passes_item_through_unfiltered():
    item = _item("https://a.com/1", "Item", "text")
    fake_store = _FakeStore(seed=_topic_vectors_seed())

    with patch("discovery.taste_vectors.get_store", return_value=fake_store), \
         patch("discovery.taste_vectors.embed_text", side_effect=RuntimeError("API down")):
        survivors, costs = taste_prefilter([item], run_id="run-1")

    assert survivors == [item]
    assert any("embed failed" in c["error"] and "passed through unfiltered" in c["error"] for c in costs)


def test_recompute_topic_vectors_writes_one_entry_per_mapped_tag():
    """learning-resource has no clearly corresponding TASTE_PROFILE
    bullet (Section 0 item 1 / Section 6) -- flagged, not guessed, no
    vector computed for it. The other 5 tags all have a mapped bullet
    and get a real vector."""
    fake_store = _FakeStore()
    mapped_tags = [t for t in TOPIC_TAGS if _TAG_TO_BULLET.get(t) is not None]
    unmapped_tags = [t for t in TOPIC_TAGS if _TAG_TO_BULLET.get(t) is None]

    with patch("discovery.taste_vectors.get_store", return_value=fake_store), \
         patch("discovery.taste_vectors.embed_text", return_value=([0.1] * 6, 20)) as mock_embed:
        costs = recompute_topic_vectors("current profile text")

    assert unmapped_tags == ["learning-resource"]
    assert set(fake_store._data.keys()) == set(mapped_tags)
    assert len(fake_store._data) == len(TOPIC_TAGS) - 1
    assert mock_embed.call_count == len(mapped_tags)  # never even attempted for the unmapped tag
    assert len(costs) == len(TOPIC_TAGS)  # one cost record per tag, including the flagged one
    unmapped_cost = next(c for c in costs if "learning-resource" in (c.get("error") or ""))
    assert "no clearly corresponding TASTE_PROFILE bullet" in unmapped_cost["error"]
    for tag, value in fake_store._data.items():
        assert value["tag"] == tag
        assert value["embedding_vector"] == [0.1] * 6


def test_recompute_topic_vectors_degrades_gracefully_on_partial_failure():
    fake_store = _FakeStore()
    call_count = {"n": 0}

    def _flaky_embed(text):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("rate limited")
        return [0.2] * 6, 15

    mapped_tags = [t for t in TOPIC_TAGS if _TAG_TO_BULLET.get(t) is not None]

    with patch("discovery.taste_vectors.get_store", return_value=fake_store), \
         patch("discovery.taste_vectors.embed_text", side_effect=_flaky_embed):
        costs = recompute_topic_vectors("profile text")

    # 5 mapped tags attempted, 1 of those fails -> 4 written; 1 unmapped
    # tag never attempted at all -> 0 written for it either way.
    assert len(fake_store._data) == len(mapped_tags) - 1
    errored = [c for c in costs if c.get("error")]
    assert len(errored) == 2  # 1 embed failure + 1 unmapped-tag flag
    assert any("rate limited" in c["error"] for c in errored)
    assert any("no clearly corresponding TASTE_PROFILE bullet" in c["error"] for c in errored)
