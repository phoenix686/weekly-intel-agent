"""
Ad-hoc bypass re-verification (batch2-dedup-taste-spec.md Section 10):
process-adhoc-input-node (Checkpoint 3) was marked passing before
semantic dedup / taste pre-filter existed as code -- its evidence cannot
have exercised "does an ad-hoc item skip these two filters", since there
was nothing to skip yet. This closes that gap directly against the real
current cluster_dedupe_node, which implements the bypass as one
source-based split (not a duplicated check inside each filter).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import discovery.nodes.cluster_dedupe as cluster_dedupe_mod
from discovery.nodes.cluster_dedupe import cluster_dedupe_node


def _raw_item(url, source, text="some content"):
    return {
        "source": source, "url": url, "title": text[:80], "text": text,
        "fetched_at": "2026-07-16T00:00:00+00:00", "author_name": "", "author_handle": "",
        "is_thread": False, "thread_contents": None, "expanded_urls": [],
    }


def _state(raw_items):
    return {
        "raw_items": raw_items, "clustered_items": [], "scored_items": [],
        "run_id": "run-1", "stage": "start", "costs": [], "errors": [],
        "source_context": "sunday",
    }


def test_adhoc_item_bypasses_both_filters_with_zero_embed_calls():
    adhoc_item = _raw_item("adhoc:abc123", "adhoc_telegram", "something Pooja texted about")
    blog_item = _raw_item("https://blog.example.com/post", "blog_scrape", "a scraped blog post")

    with patch.object(cluster_dedupe_mod, "filter_unseen", side_effect=lambda items: (items, [])), \
         patch.object(cluster_dedupe_mod, "dedupe_semantic", wraps=None) as mock_dedupe, \
         patch.object(cluster_dedupe_mod, "taste_prefilter", wraps=None) as mock_taste:
        mock_dedupe.return_value = ([blog_item], [])
        mock_taste.return_value = ([blog_item], [])

        result = cluster_dedupe_node(_state([adhoc_item, blog_item]))

    # Both filters were called with ONLY the blog item -- adhoc never passed in
    dedupe_call_items = mock_dedupe.call_args[0][0]
    taste_call_items = mock_taste.call_args[0][0]
    assert adhoc_item not in dedupe_call_items
    assert all(i["source"] != "adhoc_telegram" for i in dedupe_call_items)
    assert adhoc_item not in taste_call_items

    # And the adhoc item still ends up in the final output, untouched
    result_urls = {i["url"] for i in result["clustered_items"]}
    assert "adhoc:abc123" in result_urls
    assert "https://blog.example.com/post" in result_urls


def test_adhoc_only_run_never_calls_dedupe_or_taste_with_any_items():
    adhoc_item = _raw_item("adhoc:xyz", "adhoc_telegram", "a queued ad-hoc message")

    with patch.object(cluster_dedupe_mod, "filter_unseen", side_effect=lambda items: (items, [])), \
         patch.object(cluster_dedupe_mod, "dedupe_semantic", return_value=([], [])) as mock_dedupe, \
         patch.object(cluster_dedupe_mod, "taste_prefilter", return_value=([], [])) as mock_taste:
        result = cluster_dedupe_node(_state([adhoc_item]))

    assert mock_dedupe.call_args[0][0] == []
    assert mock_taste.call_args[0][0] == []
    assert result["clustered_items"] == [
        i for i in result["clustered_items"] if i["url"] == "adhoc:xyz"
    ]
    assert len(result["clustered_items"]) == 1
