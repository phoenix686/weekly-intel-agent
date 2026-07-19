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
    text, item_map = format_digest([], RUN_ID)
    assert text == "🤖 <b>Daily Digest</b>\n\n<i>Nothing new today.</i>"
    assert item_map == {}


def test_all_dropped_returns_nothing_today():
    items = [_item(keep=False), _item(keep=False)]
    text, item_map = format_digest(items, RUN_ID)
    assert text == "🤖 <b>Daily Digest</b>\n\n<i>Nothing new today.</i>"
    assert item_map == {}


def test_only_kept_items_appear():
    items = [_item(keep=True, title="Keep me"), _item(keep=False, title="Drop me")]
    text, item_map = format_digest(items, RUN_ID)
    assert "Keep me" in text
    assert "Drop me" not in text


def test_underscore_no_longer_needs_escaping():
    """The same root cause as the real 2026-07-19 send_telegram_plan 400
    (docs/WORKFLOW.md): this file independently escaped underscores with
    MarkdownV2 syntax while sending under legacy v1 Markdown, which has
    no escape mechanism at all. HTML mode doesn't reserve '_' -- it must
    render as a plain literal character now, no escaping applied."""
    items = [_item(keep=True, reasoning="Uses score_node and run_id internally.")]
    text, item_map = format_digest(items, RUN_ID)
    assert "score_node and run_id internally" in text
    assert "\\_" not in text


def test_ampersand_in_title_is_html_escaped():
    items = [_item(keep=True, title="Research & Compare Tools")]
    text, item_map = format_digest(items, RUN_ID)
    assert "Research &amp; Compare Tools" in text


def test_exactly_15_kept_items_all_appear():
    items = [_item(keep=True, title=f"Item {i}") for i in range(15)]
    text, item_map = format_digest(items, RUN_ID)
    for i in range(15):
        assert f"Item {i}" in text


def test_16_kept_items_only_15_appear():
    items = [_item(keep=True, title=f"Item {i}") for i in range(16)]
    text, item_map = format_digest(items, RUN_ID)
    assert "Item 15" not in text
    for i in range(15):
        assert f"Item {i}" in text


def test_footer_counts_all_scored_not_just_kept():
    kept = [_item(keep=True) for _ in range(3)]
    dropped = [_item(keep=False) for _ in range(2)]
    text, item_map = format_digest(kept + dropped, RUN_ID)
    assert "5 scored · 3 kept" in text


def test_footer_uses_first_8_chars_of_run_id():
    text, item_map = format_digest([_item(keep=True)], RUN_ID)
    assert "run: abc12345" in text


def test_tags_rendered_as_html_code_tags():
    items = [_item(keep=True, tags=["agentic-engineering", "evals"])]
    text, item_map = format_digest(items, RUN_ID)
    assert "<code>agentic-engineering</code>" in text
    assert "<code>evals</code>" in text


def test_reasoning_wrapped_in_italic_tag():
    items = [_item(keep=True, reasoning="This is the reasoning.")]
    text, item_map = format_digest(items, RUN_ID)
    assert "<i>This is the reasoning.</i>" in text


def test_title_rendered_as_html_link():
    items = [_item(keep=True, title="Article Title")]
    text, item_map = format_digest(items, RUN_ID)
    assert '<a href="https://example.com">Article Title</a>' in text


def test_item_map_stores_raw_unescaped_text_not_rendered_html():
    items = [_item(keep=True, title="R&D notes", reasoning="Uses <brackets> and & signs.")]
    text, item_map = format_digest(items, RUN_ID)
    assert item_map[1]["title"] == "R&D notes"
    assert item_map[1]["reasoning"] == "Uses <brackets> and & signs."
    assert "R&amp;D notes" in text
    assert "&lt;brackets&gt;" in text


def test_missing_title_falls_back_to_text():
    item = {
        "url": "https://example.com", "text": "Full body text used as fallback title.",
        "author_name": "Author", "author_handle": "author",
        "fetched_at": "2026-01-01T00:00:00Z", "is_thread": False,
        "thread_contents": None, "expanded_urls": [], "source": "twillot_bootstrap",
        "duplicate_count": 1, "keep": True, "reasoning": "Good content.",
        "tags": ["agentic-engineering"],
    }
    text, item_map = format_digest([item], RUN_ID)
    assert "Full body text used as fallback title." in text


def test_item_map_keyed_by_display_number_with_correct_fields():
    items = [_item(keep=True, title="Keep me", tags=["evals"])]
    text, item_map = format_digest(items, RUN_ID)
    assert set(item_map.keys()) == {1}
    assert item_map[1]["url"] == "https://example.com"
    assert item_map[1]["title"] == "Keep me"
    assert item_map[1]["tags"] == ["evals"]
    assert item_map[1]["reasoning"] == "Good content."
