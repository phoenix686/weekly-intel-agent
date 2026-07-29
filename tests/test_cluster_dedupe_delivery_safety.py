from unittest.mock import patch

from discovery.nodes.cluster_dedupe import cluster_dedupe_node


def _raw_item(url: str):
    return {
        "source": "blog_scrape",
        "url": url,
        "title": "Candidate",
        "text": "candidate content",
        "fetched_at": "2026-07-16T00:00:00+00:00",
        "author_name": "",
        "author_handle": "",
        "is_thread": False,
        "thread_contents": None,
        "expanded_urls": [],
    }


def test_uncategorized_item_is_not_marked_seen_before_delivery():
    item = _raw_item("https://example.com/unclassified")
    uncategorized = {
        **item,
        "duplicate_count": 1,
        "best_tag": "evals",
        "similarity_score": 0.2,
    }
    state = {
        "raw_items": [item],
        "clustered_items": [],
        "scored_items": [],
        "run_id": "run-1",
        "costs": [],
        "errors": [],
        "source_context": "daily",
        "dry_run": False,
    }

    with patch(
        "discovery.nodes.cluster_dedupe.filter_unseen",
        side_effect=lambda items: (items, []),
    ), patch(
        "discovery.nodes.cluster_dedupe.dedupe_semantic",
        return_value=([uncategorized], []),
    ), patch(
        "discovery.nodes.cluster_dedupe.taste_prefilter",
        return_value=([], [uncategorized], []),
    ), patch("discovery.nodes.cluster_dedupe.record_node_summary"):
        result = cluster_dedupe_node(state)

    assert result["uncategorized_items"] == [uncategorized]
