"""
cluster_dedupe_node's mark_seen() coverage for uncategorized_items
(2026-07-26 fix). Real bug found investigating run 08b5d13b: items
taste_prefilter drops into "uncategorized" never reach score_node, and
score_node's mark_seen() call only ever covers all_scored -- so nothing
in the pipeline marked an uncategorized item seen, and a below-threshold
item that's still fetchable next run (a dormant source's same top-N
posts, in particular) resurfaced as "new" every single run, indefinitely.

Mirrors tests/test_score_node.py's mark_seen/dry_run coverage pattern.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import discovery.nodes.cluster_dedupe as cluster_dedupe_mod
from discovery.nodes.cluster_dedupe import cluster_dedupe_node


def _raw_item(url, source="blog_scrape", text="some content"):
    return {
        "source": source, "url": url, "title": text[:80], "text": text,
        "fetched_at": "2026-07-16T00:00:00+00:00", "author_name": "", "author_handle": "",
        "is_thread": False, "thread_contents": None, "expanded_urls": [],
    }


def _uncategorized(url, best_tag="new-tool-launch", similarity_score=0.17):
    return {**_raw_item(url), "best_tag": best_tag, "similarity_score": similarity_score}


def _state(raw_items, dry_run=None):
    state = {
        "raw_items": raw_items, "clustered_items": [], "scored_items": [],
        "run_id": "run-1", "costs": [], "errors": [],
        "source_context": "sunday",
    }
    if dry_run is not None:
        state["dry_run"] = dry_run
    return state


def _patched_pipeline(relevant, uncategorized):
    return (
        patch.object(cluster_dedupe_mod, "filter_unseen", side_effect=lambda items: (items, [])),
        patch.object(cluster_dedupe_mod, "dedupe_semantic", return_value=(relevant + uncategorized, [])),
        patch.object(cluster_dedupe_mod, "taste_prefilter", return_value=(relevant, uncategorized, [])),
        patch.object(cluster_dedupe_mod, "record_node_summary"),
        patch.object(cluster_dedupe_mod, "mark_seen"),
    )


def test_mark_seen_called_with_uncategorized_urls():
    relevant_item = _raw_item("https://blog.example.com/relevant")
    uncategorized_item = _uncategorized("https://blog.example.com/uncategorized")

    p_unseen, p_dedupe, p_taste, p_summary, p_mark_seen = _patched_pipeline([relevant_item], [uncategorized_item])
    with p_unseen, p_dedupe, p_taste, p_summary, p_mark_seen as mock_mark_seen:
        cluster_dedupe_node(_state([relevant_item, uncategorized_item]))

    mock_mark_seen.assert_called_once()
    (called_urls,), _ = mock_mark_seen.call_args
    assert called_urls == ["https://blog.example.com/uncategorized"]


def test_mark_seen_not_called_with_relevant_item_urls():
    """Only uncategorized items are marked here -- relevant items are
    marked later by score_node once scoring actually completes, same
    two-stage contract as before this fix."""
    relevant_item = _raw_item("https://blog.example.com/relevant")
    uncategorized_item = _uncategorized("https://blog.example.com/uncategorized")

    p_unseen, p_dedupe, p_taste, p_summary, p_mark_seen = _patched_pipeline([relevant_item], [uncategorized_item])
    with p_unseen, p_dedupe, p_taste, p_summary, p_mark_seen as mock_mark_seen:
        cluster_dedupe_node(_state([relevant_item, uncategorized_item]))

    (called_urls,), _ = mock_mark_seen.call_args
    assert "https://blog.example.com/relevant" not in called_urls


def test_no_uncategorized_items_calls_mark_seen_with_empty_list():
    relevant_item = _raw_item("https://blog.example.com/relevant")

    p_unseen, p_dedupe, p_taste, p_summary, p_mark_seen = _patched_pipeline([relevant_item], [])
    with p_unseen, p_dedupe, p_taste, p_summary, p_mark_seen as mock_mark_seen:
        cluster_dedupe_node(_state([relevant_item]))

    mock_mark_seen.assert_called_once_with([])


def test_dry_run_skips_mark_seen_for_uncategorized_items():
    """Same dry_run contract as score_node's mark_seen() -- manual/dry
    testing must not permanently burn through the real seen_items pool."""
    uncategorized_item = _uncategorized("https://blog.example.com/uncategorized")

    p_unseen, p_dedupe, p_taste, p_summary, p_mark_seen = _patched_pipeline([], [uncategorized_item])
    with p_unseen, p_dedupe, p_taste, p_summary, p_mark_seen as mock_mark_seen:
        cluster_dedupe_node(_state([uncategorized_item], dry_run=True))

    mock_mark_seen.assert_not_called()


def test_uncategorized_items_round_trip_marks_them_seen_in_real_store():
    """End-to-end through the real seen_items module (mark_seen only
    mocked out of the pipeline above -- here it's the real thing): a
    taste-prefilter drop must actually become invisible to a subsequent
    filter_unseen() call, proving the fix closes the resurfacing gap for
    real, not just that mark_seen() gets *called*."""
    from tests.test_seen_items import _FakeStore
    uncategorized_item = _uncategorized("https://blog.example.com/uncategorized")

    fake_store = _FakeStore()
    with patch.object(cluster_dedupe_mod, "filter_unseen", side_effect=lambda items: (items, [])), \
         patch.object(cluster_dedupe_mod, "dedupe_semantic", return_value=([uncategorized_item], [])), \
         patch.object(cluster_dedupe_mod, "taste_prefilter", return_value=([], [uncategorized_item], [])), \
         patch.object(cluster_dedupe_mod, "record_node_summary"), \
         patch("discovery.seen_items.get_store", return_value=fake_store):
        cluster_dedupe_node(_state([uncategorized_item]))

    from discovery.seen_items import filter_unseen
    with patch("discovery.seen_items.get_store", return_value=fake_store):
        unseen, seen_urls = filter_unseen([uncategorized_item])

    assert unseen == []
    assert seen_urls == ["https://blog.example.com/uncategorized"]
