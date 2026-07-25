import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from langgraph.store.base import GetOp, PutOp

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

    def batch(self, ops):
        """Real PostgresStore.batch() dispatches a list of Get/PutOp in
        one call -- mirrored here via the existing put() so behavior
        stays correct, real batching or not."""
        results = []
        for op in ops:
            if isinstance(op, PutOp):
                self.put(op.namespace, op.key, op.value)
                results.append(None)
            elif isinstance(op, GetOp):
                results.append(self._data.get(op.key))
        return results


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
         patch("discovery.taste_vectors.embed_texts", return_value=([strong_agentic_vector], [10])):
        survivors, uncategorized, costs = taste_prefilter([item], run_id="run-1")

    assert survivors == [item]
    assert uncategorized == []
    assert not any(c.get("error") for c in costs)


def test_item_below_threshold_against_every_topic_is_dropped():
    item = _item("https://a.com/1", "Unrelated item", "nothing to do with any topic")
    fake_store = _FakeStore(seed=_topic_vectors_seed())

    with patch("discovery.taste_vectors.get_store", return_value=fake_store), \
         patch("discovery.taste_vectors.embed_texts", return_value=([_IRRELEVANT_VECTOR], [10])):
        survivors, uncategorized, costs = taste_prefilter([item], run_id="run-1")

    assert survivors == []
    assert any("uncategorized" in c["error"] for c in costs)


def test_item_below_threshold_is_returned_uncategorized_not_silently_dropped():
    """2026-07-22, lightweight-uncategorized-flagging: a sub-threshold item
    is no longer just a cost-record error -- it comes back as a real
    UncategorizedItem carrying its own url/title/text plus best_tag and
    similarity_score, for assemble_digest/assemble_plan's trailing
    section and Telegram feedback routing."""
    item = _item("https://a.com/1", "Unrelated item", "nothing to do with any topic")
    fake_store = _FakeStore(seed=_topic_vectors_seed())

    with patch("discovery.taste_vectors.get_store", return_value=fake_store), \
         patch("discovery.taste_vectors.embed_texts", return_value=([_IRRELEVANT_VECTOR], [10])):
        survivors, uncategorized, costs = taste_prefilter([item], run_id="run-1")

    assert len(uncategorized) == 1
    assert uncategorized[0]["url"] == "https://a.com/1"
    assert uncategorized[0]["title"] == "Unrelated item"
    assert uncategorized[0]["best_tag"] in {
        "agentic-engineering", "memory-systems", "llm-tooling",
        "evals", "learning-resource", "distributed-systems",
    }
    assert uncategorized[0]["similarity_score"] < 0.30


def test_empty_topic_vector_store_lets_everything_through():
    item = _item("https://a.com/1", "Anything", "no topic vectors exist yet")
    fake_store = _FakeStore(seed={})

    with patch("discovery.taste_vectors.get_store", return_value=fake_store), \
         patch("discovery.taste_vectors.embed_texts") as mock_embed:
        survivors, uncategorized, costs = taste_prefilter([item], run_id="run-1")

    assert survivors == [item]
    assert uncategorized == []
    assert costs == []
    mock_embed.assert_not_called()  # never even tries to embed if there's nothing to compare against


def test_failed_batch_embed_call_marks_items_uncategorized_not_auto_passed():
    """2026-07-25 fix: a hard embed failure used to return every item as
    if it had PASSED the taste filter -- not evidence of relevance, and
    the exact mechanism that let two apparently-good days (2026-07-23)
    turn out to be the filter silently never running (a real 76,675-char
    MarkTechPost article 400ing the whole batch). Failed items are now
    marked uncategorized instead -- flagged, not silently auto-passed --
    and the failure itself is durably recorded."""
    item_a = _item("https://a.com/1", "Item A", "text a")
    item_b = _item("https://b.com/1", "Item B", "text b")
    fake_store = _FakeStore(seed=_topic_vectors_seed())

    with patch("discovery.taste_vectors.get_store", return_value=fake_store), \
         patch("discovery.embeddings.get_store", return_value=fake_store), \
         patch("discovery.taste_vectors.embed_texts", side_effect=RuntimeError("model broken")):
        survivors, uncategorized, costs = taste_prefilter([item_a, item_b], run_id="run-1")

    assert survivors == []
    assert {u["url"] for u in uncategorized} == {"https://a.com/1", "https://b.com/1"}
    assert all(u["best_tag"] == "embed_failed" and u["similarity_score"] == 0.0 for u in uncategorized)
    assert len(costs) == 2
    assert all("embed failed" in c["error"] and "marked uncategorized" in c["error"] for c in costs)

    failure_puts = [v for v in fake_store._data.values() if isinstance(v, dict) and v.get("module") == "taste_prefilter"]
    assert len(failure_puts) == 1
    assert failure_puts[0]["run_id"] == "run-1"
    assert failure_puts[0]["item_count"] == 2
    assert failure_puts[0]["error"] == "model broken"


def test_recompute_topic_vectors_writes_one_entry_per_mapped_tag():
    """learning-resource and course have no clearly corresponding
    TASTE_PROFILE bullet (Section 0 item 1 / Section 6; course added for
    the Courses digest section, Checkpoint: Sunday plan LLM
    prioritization, sub-phase 1 -- same "format, not topic" reasoning) --
    flagged, not guessed, no vector computed for either. The other 6 tags
    all have a mapped bullet and get a real vector."""
    fake_store = _FakeStore()
    mapped_tags = [t for t in TOPIC_TAGS if _TAG_TO_BULLET.get(t) is not None]
    unmapped_tags = [t for t in TOPIC_TAGS if _TAG_TO_BULLET.get(t) is None]

    with patch("discovery.taste_vectors.get_store", return_value=fake_store), \
         patch("discovery.taste_vectors.embed_text", return_value=([0.1] * 6, 20)) as mock_embed:
        costs = recompute_topic_vectors("current profile text")

    assert unmapped_tags == ["course", "learning-resource"]
    assert set(fake_store._data.keys()) == set(mapped_tags)
    assert len(fake_store._data) == len(TOPIC_TAGS) - 2
    assert mock_embed.call_count == len(mapped_tags)  # never even attempted for the unmapped tags
    assert len(costs) == len(TOPIC_TAGS)  # one cost record per tag, including the flagged ones
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

    # 6 mapped tags attempted, 1 of those fails -> 5 written; 2 unmapped
    # tags never attempted at all -> 0 written for them either way.
    assert len(fake_store._data) == len(mapped_tags) - 1
    errored = [c for c in costs if c.get("error")]
    assert len(errored) == 3  # 1 embed failure + 2 unmapped-tag flags
    assert any("rate limited" in c["error"] for c in errored)
    assert any("no clearly corresponding TASTE_PROFILE bullet" in c["error"] for c in errored)
