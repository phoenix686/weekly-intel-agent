import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daily.nodes.assemble_digest import format_digest, MAX_DIGEST_ITEMS


def _item(keep: bool, title: str = "A title", reasoning: str = "Good content.", tags: list[str] = None):
    return {
        "url": "https://example.com",
        "title": title,
        "text": "body text",
        "author_name": "Author",
        "author_handle": "author",
        "fetched_at": "2026-01-01T00:00:00Z",
        "is_thread": False,
        "thread_contents": None,
        "expanded_urls": [],
        "source": "twillot_bootstrap",
        "duplicate_count": 1,
        "keep": keep,
        "reasoning": reasoning,
        "tags": tags or ["agentic-engineering"],
    }


RUN_ID = "abc12345-0000-0000-0000-000000000000"


def test_empty_input_returns_nothing_today():
    result = format_digest([], RUN_ID)
    assert result == "🤖 *Daily Digest*\n\n_Nothing new today._"


def test_all_dropped_returns_nothing_today():
    items = [_item(keep=False), _item(keep=False)]
    result = format_digest(items, RUN_ID)
    assert result == "🤖 *Daily Digest*\n\n_Nothing new today._"


def test_only_kept_items_appear():
    items = [_item(keep=True, title="Keep me"), _item(keep=False, title="Drop me")]
    result = format_digest(items, RUN_ID)
    assert "Keep me" in result
    assert "Drop me" not in result


def test_underscores_in_reasoning_are_escaped():
    items = [_item(keep=True, reasoning="Uses score_node and run_id internally.")]
    result = format_digest(items, RUN_ID)
    assert r"score\_node" in result
    assert r"run\_id" in result
    assert "score_node" not in result.split("reasoning")[0]  # raw underscore gone from body


def test_exactly_15_kept_items_all_appear():
    items = [_item(keep=True, title=f"Item {i}") for i in range(15)]
    result = format_digest(items, RUN_ID)
    for i in range(15):
        assert f"Item {i}" in result


def test_16_kept_items_only_15_appear():
    items = [_item(keep=True, title=f"Item {i}") for i in range(16)]
    result = format_digest(items, RUN_ID)
    assert "Item 15" not in result
    for i in range(15):
        assert f"Item {i}" in result


def test_footer_counts_all_scored_not_just_kept():
    kept = [_item(keep=True) for _ in range(3)]
    dropped = [_item(keep=False) for _ in range(2)]
    result = format_digest(kept + dropped, RUN_ID)
    assert "5 scored · 3 kept" in result


def test_footer_uses_first_8_chars_of_run_id():
    result = format_digest([_item(keep=True)], RUN_ID)
    assert "run: abc12345" in result


def test_tags_rendered_as_backtick_code():
    items = [_item(keep=True, tags=["agentic-engineering", "evals"])]
    result = format_digest(items, RUN_ID)
    assert "`agentic-engineering`" in result
    assert "`evals`" in result


def test_reasoning_wrapped_in_italic():
    items = [_item(keep=True, reasoning="This is the reasoning.")]
    result = format_digest(items, RUN_ID)
    assert "_This is the reasoning._" in result


def test_missing_title_falls_back_to_text():
    item = {
        "url": "https://example.com", "text": "Full body text used as fallback title.",
        "author_name": "Author", "author_handle": "author",
        "fetched_at": "2026-01-01T00:00:00Z", "is_thread": False,
        "thread_contents": None, "expanded_urls": [], "source": "twillot_bootstrap",
        "duplicate_count": 1, "keep": True, "reasoning": "Good content.",
        "tags": ["agentic-engineering"],
    }
    result = format_digest([item], RUN_ID)
    assert "Full body text used as fallback title." in result
