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


def _uncategorized(url="https://example.com/uncategorized", title="An uncategorized title",
                    best_tag="new-tool-launch", similarity_score=0.186):
    return {
        "url": url, "title": title, "text": "body text of the uncategorized item",
        "author_name": "Author", "author_handle": "author",
        "fetched_at": "2026-01-01T00:00:00Z", "is_thread": False, "thread_contents": None,
        "expanded_urls": [], "source": "blog_scrape", "duplicate_count": 1,
        "best_tag": best_tag, "similarity_score": similarity_score,
    }


# ── Uncategorized-flagging (2026-07-22, lightweight-uncategorized-flagging) ────
# Real demonstration case: today's actual Latent Space AI-cybersecurity item.
# Originally taste-prefiltered at cosine=0.186 vs "new-tool-launch" (threshold
# 0.30) when only the RSS <description> teaser (51 chars) was embedded. After
# the same-day content-truncation fix (rss_common.py now reads the full
# <content:encoded> article, 29,364 chars), a LIVE re-check against the real
# current topic vectors moved the score to 0.251 -- genuinely closer, but
# still below 0.30: this item needs BOTH fixes, not just one. Confirmed by
# direct execution against the real store/embeddings, not assumed.

_REAL_CYBERSECURITY_ITEM = _uncategorized(
    url="https://www.latent.space/p/ainews-ai-cybersecurity-becomes-top",
    title="[AINews] AI Cybersecurity becomes top of mind",
    best_tag="new-tool-launch",
    similarity_score=0.251,
)


def test_uncategorized_item_no_longer_vanishes_before_after():
    """BEFORE (old behavior, no uncategorized_items param): the item is
    invisible -- format_digest has no way to know it ever existed.
    AFTER: passing it through the new parameter surfaces it."""
    before_text, before_map = format_digest([], RUN_ID)
    assert "cybersecurity" not in before_text.lower()
    assert before_map == {}

    after_text, after_map = format_digest([], RUN_ID, uncategorized_items=[_REAL_CYBERSECURITY_ITEM])
    assert "AI Cybersecurity becomes top of mind" in after_text
    assert "https://www.latent.space/p/ainews-ai-cybersecurity-becomes-top" in after_text
    assert "new-tool-launch" in after_text
    assert "0.251" in after_text
    assert after_map[1]["url"] == "https://www.latent.space/p/ainews-ai-cybersecurity-becomes-top"
    assert after_map[1]["tags"] == ["uncategorized"]


def test_uncategorized_section_appears_even_with_zero_kept_items():
    text, item_map = format_digest([], RUN_ID, uncategorized_items=[_uncategorized()])
    assert "Nothing new today" in text  # kept-section placeholder still shows
    assert "1 item(s) didn't match any existing topic" in text
    assert set(item_map.keys()) == {1}


def test_uncategorized_numbering_continues_after_kept_items():
    kept = [_item(keep=True, title="Kept item")]
    uncategorized = [_uncategorized(title="Uncategorized item")]
    text, item_map = format_digest(kept, RUN_ID, uncategorized_items=uncategorized)
    assert set(item_map.keys()) == {1, 2}
    assert item_map[1]["title"] == "Kept item"
    assert item_map[2]["title"] == "Uncategorized item"
    assert "1. <a" in text
    assert "2. <a" in text


def test_uncategorized_item_map_entry_carries_best_tag_and_score_in_reasoning():
    text, item_map = format_digest([], RUN_ID, uncategorized_items=[_REAL_CYBERSECURITY_ITEM])
    assert "new-tool-launch" in item_map[1]["reasoning"]
    assert "0.251" in item_map[1]["reasoning"]


def test_footer_includes_uncategorized_count():
    kept = [_item(keep=True)]
    text, item_map = format_digest(kept, RUN_ID, uncategorized_items=[_uncategorized(), _uncategorized()])
    assert "1 scored · 1/1 shown · 2 uncategorized" in text


def test_no_uncategorized_items_omits_the_section_entirely():
    kept = [_item(keep=True)]
    text, item_map = format_digest(kept, RUN_ID, uncategorized_items=[])
    assert "didn't match any existing topic" not in text
    assert "0 uncategorized" in text  # footer still reports the real (zero) count


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
    assert "5 scored · 3/3 shown" in text


def test_footer_shown_count_reflects_max_digest_items_truncation():
    """The real bug this session found: with more kept items than
    MAX_DIGEST_ITEMS, the footer must show the TRUNCATED shown count, not
    the full kept count -- previously the footer claimed the full kept
    number even though only MAX_DIGEST_ITEMS were actually rendered."""
    items = [_item(keep=True, title=f"Item {i}") for i in range(22)]
    text, item_map = format_digest(items, RUN_ID)
    assert f"22 scored · {MAX_DIGEST_ITEMS}/22 shown" in text


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
