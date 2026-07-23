import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from langgraph.store.base import GetOp, PutOp

from discovery.semantic_dedup import dedupe_semantic, _NAMESPACE, _FAILURES_NAMESPACE


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


def _item(url, title, text, fetched_at="2026-07-16T00:00:00+00:00", source="blog_scrape"):
    return {"url": url, "title": title, "text": text, "author_name": "", "author_handle": "",
            "fetched_at": fetched_at, "is_thread": False, "thread_contents": None,
            "expanded_urls": [], "source": source, "duplicate_count": 1}


def _embed_texts_side_effect(vectors_by_text):
    """Batched embed_texts() fixture -- looks up each text in the batch
    independently and returns (vectors, per_item_tokens), matching the
    real function's post-batching signature."""
    def _fn(texts):
        vectors = []
        for text in texts:
            for key, vec in vectors_by_text.items():
                if key in text:
                    vectors.append(vec)
                    break
            else:
                raise KeyError(f"no fixture vector for text containing: {text[:50]}")
        return vectors, [10] * len(texts)
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
         patch("discovery.semantic_dedup.embed_texts", side_effect=_embed_texts_side_effect(vectors)):
        survivors, costs = dedupe_semantic([earlier, later_but_fuller], run_id="run-1")

    assert len(survivors) == 1
    assert survivors[0]["url"] == "https://a.com/1"  # earlier-published (first-seen) item won, despite shorter text
    drop_errors = [c["error"] for c in costs if c.get("error")]
    assert any("dropped as near-verbatim duplicate of" in e and "a.com" in e for e in drop_errors)


def test_within_run_duplicate_arriving_earlier_in_list_but_published_later_gets_swapped_out():
    """The opposite ordering -- first-seen item is actually published
    LATER -- confirms the swap direction is driven by fetched_at, not by
    which item happened to be processed first."""
    later = _item("https://a.com/1", "Story A", "processed first", fetched_at="2026-07-16T14:00:00+00:00")
    earlier = _item("https://b.com/1", "Story A elsewhere", "processed second", fetched_at="2026-07-16T08:00:00+00:00")

    fake_store = _FakeStore()
    vectors = {"processed first": [1.0, 0.0], "processed second": [0.999, 0.001]}

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_texts", side_effect=_embed_texts_side_effect(vectors)):
        survivors, costs = dedupe_semantic([later, earlier], run_id="run-1")

    assert len(survivors) == 1
    assert survivors[0]["url"] == "https://b.com/1"  # swapped in despite arriving second in the list


def test_items_below_threshold_both_survive():
    a = _item("https://a.com/1", "Story A", "text about agents")
    b = _item("https://b.com/1", "Story B", "text about databases")

    fake_store = _FakeStore()
    vectors = {"about agents": [1.0, 0.0], "about databases": [0.0, 1.0]}  # orthogonal, cosine 0.0

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_texts", side_effect=_embed_texts_side_effect(vectors)):
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
         patch("discovery.semantic_dedup.embed_texts", side_effect=_embed_texts_side_effect(vectors)):
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
         patch("discovery.semantic_dedup.embed_texts", side_effect=_embed_texts_side_effect(vectors)):
        survivors, costs = dedupe_semantic([item], run_id="run-1")

    assert len(survivors) == 1  # stale entry excluded from comparison, so no match -> survives
    assert "https://stale.com/1" in fake_store.deleted  # lazily cleaned up


def test_failed_batch_embed_call_passes_all_items_through_unfiltered():
    """embed_texts() is called once for the whole batch now (not once per
    item) -- a failure degrades every item in the batch at once, not just
    one, since a local-model failure realistically means the model itself
    is broken."""
    item_a = _item("https://a.com/1", "Story A", "some text")
    item_b = _item("https://b.com/1", "Story B", "other text")
    fake_store = _FakeStore()

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_texts", side_effect=RuntimeError("model broken")):
        survivors, costs = dedupe_semantic([item_a, item_b], run_id="run-1")

    assert survivors == [item_a, item_b]
    assert len(costs) == 2
    assert all("embed failed" in c["error"] and "passed through unfiltered" in c["error"] for c in costs)
    # No vectors were produced, so nothing goes into recent_item_embeddings
    # or prefilter_drops -- but the failure itself must be durably recorded
    # (2026-07-23 fix: this exact silent-degradation path is what let
    # semantic dedup run as a no-op for weeks, undetected, after the
    # NVIDIA provider swap).
    assert [p for p in fake_store.puts if p[0] != _FAILURES_NAMESPACE] == []
    failure_puts = [p for p in fake_store.puts if p[0] == _FAILURES_NAMESPACE]
    assert len(failure_puts) == 1
    failure_record = failure_puts[0][2]
    assert failure_record["run_id"] == "run-1"
    assert failure_record["item_count"] == 2
    assert failure_record["error"] == "model broken"
    assert "occurred_at" in failure_record


# ── Content-overlap tier (2026-07-23): same-story-different-dedicated-article ──
# Calibrated against real embeddings of the real confirmed pairs: Laguna S 2.1
# HF-repo vs. MarkTechPost write-up (within-run, cosine 0.6423) and the Cursor
# Router MarkTechPost-vs-x.com pair (cross-run, cosine 0.6356) -- both above
# _CONTENT_OVERLAP_THRESHOLD (0.60) and above the highest real control pair
# observed that session (0.5435, an unrelated roundup/MarkTechPost pair).
# These fixture vectors reproduce those exact real cosines via two 2D unit
# vectors at the corresponding angle, not just an arbitrary "high" number.

def _unit_vector_at_cosine(cosine: float) -> list[float]:
    return [cosine, (1 - cosine ** 2) ** 0.5]


def test_within_run_content_overlap_drops_dedicated_duplicate_article():
    """Real pair, real number: Laguna S 2.1 covered by both a Hugging Face
    repo page and a separate MarkTechPost write-up, same run -- confirmed
    real cosine 0.6423 this session, below _THRESHOLD (0.90) but above
    _CONTENT_OVERLAP_THRESHOLD (0.60)."""
    earlier = _item("https://huggingface.co/poolside/Laguna-S-2.1", "Laguna S 2.1 (Hugging Face Repo)",
                     "model repo", fetched_at="2026-07-21T08:00:00+00:00")
    later = _item("https://www.marktechpost.com/laguna-s-2-1", "Poolside Releases Laguna S 2.1",
                   "writeup", fetched_at="2026-07-21T14:00:00+00:00")

    fake_store = _FakeStore()
    vectors = {"model repo": [1.0, 0.0], "writeup": _unit_vector_at_cosine(0.6423)}

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_texts", side_effect=_embed_texts_side_effect(vectors)):
        survivors, costs = dedupe_semantic([earlier, later], run_id="run-1")

    assert len(survivors) == 1
    assert survivors[0]["url"] == "https://huggingface.co/poolside/Laguna-S-2.1"  # earlier-published wins
    drop_errors = [c["error"] for c in costs if c.get("error")]
    assert any("content-overlap duplicate" in e for e in drop_errors)


def test_cross_run_content_overlap_drops_new_dedicated_duplicate_article():
    """Real pair, real number: Cursor Router covered by a MarkTechPost
    article in one already-completed run, then a different x.com URL
    ~10 hours later in the next run -- confirmed real cosine 0.6356 this
    session. Cross-run: the new item is dropped unconditionally (the
    earlier run's digest already shipped, nothing to swap)."""
    window_seed = {
        "https://www.marktechpost.com/cursor-router": {
            "item_id": "https://www.marktechpost.com/cursor-router",
            "url": "https://www.marktechpost.com/cursor-router",
            "embedding_vector": [1.0, 0.0],
            "fetched_at": "2026-07-23T00:00:00+00:00",
            "scored_at": datetime.now(timezone.utc).isoformat(),
            "is_roundup": False,
        },
    }
    fake_store = _FakeStore(seed=window_seed)
    new_item = _item("https://x.com/cursor_ai/status/123", "Cursor Router", "a tweet about the same launch")
    vectors = {"a tweet about the same launch": _unit_vector_at_cosine(0.6356)}

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_texts", side_effect=_embed_texts_side_effect(vectors)):
        survivors, costs = dedupe_semantic([new_item], run_id="run-2")

    assert survivors == []
    drop_errors = [c["error"] for c in costs if c.get("error")]
    assert any("content-overlap duplicate of previously-seen" in e and "marktechpost" in e for e in drop_errors)


def test_control_pairs_below_content_overlap_threshold_both_survive():
    """Real control number from this session: the highest-scoring unrelated
    pair observed was 0.5435 (roundup vs. an unrelated MarkTechPost
    article) -- below _CONTENT_OVERLAP_THRESHOLD (0.60). Confirms the new
    lower tier doesn't over-trigger on genuinely unrelated content."""
    a = _item("https://a.com/1", "EdgeBench Analysis", "benchmarking article")
    b = _item("https://b.com/1", "Fine-Tuning Framework Comparison", "unrelated comparison article")

    fake_store = _FakeStore()
    vectors = {"benchmarking article": [1.0, 0.0], "unrelated comparison article": _unit_vector_at_cosine(0.5435)}

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_texts", side_effect=_embed_texts_side_effect(vectors)):
        survivors, costs = dedupe_semantic([a, b], run_id="run-1")

    assert len(survivors) == 2
    assert not any(c.get("error") for c in costs)


def test_roundup_item_never_drops_a_dedicated_article_via_content_overlap():
    """Scope guard (confirmed decision, 2026-07-23): the content-overlap
    tier must never fire between a roundup-style item and an individual
    article -- Case A (roundup covers a story in passing) needs a
    fundamentally different per-story chunking approach, deferred. A
    Latent Space '[AINews]' post scoring 0.75 against a dedicated article
    (well above _CONTENT_OVERLAP_THRESHOLD) must still let both survive."""
    roundup = _item("https://www.latent.space/p/ainews-roundup", "[AINews] Weekly AI roundup",
                     "a long roundup covering many stories")
    dedicated = _item("https://example.com/one-story", "One Specific Story",
                       "a dedicated article the roundup happens to mention in passing")

    fake_store = _FakeStore()
    vectors = {
        "a long roundup covering many stories": [1.0, 0.0],
        "a dedicated article the roundup happens to mention in passing": _unit_vector_at_cosine(0.75),
    }

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_texts", side_effect=_embed_texts_side_effect(vectors)):
        survivors, costs = dedupe_semantic([roundup, dedicated], run_id="run-1")

    assert len(survivors) == 2  # NOT dropped, despite scoring well above _CONTENT_OVERLAP_THRESHOLD
    assert not any(c.get("error") for c in costs)


def test_tldr_ai_source_also_guarded_from_content_overlap_drops():
    """Same scope guard, via the source=='TLDR AI' signal (matches
    blog_sources.yaml's roundup: true config flag) rather than the
    '[AINews]' title prefix."""
    roundup = _item("https://tldr.tech/ai/2026-07-23", "TLDR AI issue", "roundup issue content",
                     source="TLDR AI")
    dedicated = _item("https://example.com/one-story", "One Specific Story", "dedicated article content")

    fake_store = _FakeStore()
    vectors = {
        "roundup issue content": [1.0, 0.0],
        "dedicated article content": _unit_vector_at_cosine(0.75),
    }

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_texts", side_effect=_embed_texts_side_effect(vectors)):
        survivors, costs = dedupe_semantic([roundup, dedicated], run_id="run-1")

    assert len(survivors) == 2
    assert not any(c.get("error") for c in costs)


def test_survivor_window_entry_persists_is_roundup_flag():
    """The content-overlap cross-run guard needs to know whether a
    PREVIOUS run's window entry was itself a roundup item -- confirms
    is_roundup is actually persisted into recent_item_embeddings, not
    just checked against the in-run item."""
    roundup = _item("https://www.latent.space/p/ainews-roundup", "[AINews] Weekly AI roundup", "roundup text")
    dedicated = _item("https://example.com/one-story", "One Specific Story", "dedicated text")

    fake_store = _FakeStore()
    vectors = {"roundup text": [1.0, 0.0], "dedicated text": [0.0, 1.0]}

    with patch("discovery.semantic_dedup.get_store", return_value=fake_store), \
         patch("discovery.semantic_dedup.embed_texts", side_effect=_embed_texts_side_effect(vectors)):
        dedupe_semantic([roundup, dedicated], run_id="run-1")

    roundup_entry = fake_store._data["https://www.latent.space/p/ainews-roundup"]
    dedicated_entry = fake_store._data["https://example.com/one-story"]
    assert roundup_entry["is_roundup"] is True
    assert dedicated_entry["is_roundup"] is False
