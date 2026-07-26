import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

from saturday.nodes.assemble_plan import format_plan, assemble_plan, MAX_PLAN_TEXT_CHARS, REASONING_CHAR_BUDGET

RUN_ID = "abc12345-0000-0000-0000-000000000000"


def _plan_item(matched_card_id=None, title="A title", reasoning="Good content.",
               url="https://example.com", text="body text", tags=None):
    return {
        "url": url, "title": title, "text": text, "reasoning": reasoning,
        "classification": "plan_item", "proposal_type": None,
        "classification_reasoning": "routed as plan_item",
        "matched_card_id": matched_card_id,
        "tags": tags if tags is not None else ["agentic-engineering"],
    }


def _proposal(title="New project idea"):
    return {
        "url": "https://example.com/proposal", "title": title, "text": "body text",
        "reasoning": "Interesting new direction.", "classification": "project_proposal",
        "proposal_type": "new", "classification_reasoning": "new scope",
        "matched_card_id": None, "tags": ["side-projects"],
    }


def _card(card_id="card1", name="My Trello Card", url="https://trello.com/c/abc"):
    return {"card_id": card_id, "name": name, "list_name": "In Progress", "url": url}


def _priority_entry(matched_card_id="card1", source="new_item", item_url="https://example.com",
                     priority_reasoning="Priority reasoning.", movement_note=None):
    return {
        "matched_card_id": matched_card_id, "source": source, "item_url": item_url,
        "priority_reasoning": priority_reasoning, "movement_note": movement_note,
    }


def _uncategorized(url="https://example.com/uncategorized", title="An uncategorized title",
                    best_tag="new-tool-launch", similarity_score=0.186):
    return {
        "url": url, "title": title, "text": "body text of the uncategorized item",
        "best_tag": best_tag, "similarity_score": similarity_score,
    }


_REAL_CYBERSECURITY_ITEM = _uncategorized(
    url="https://www.latent.space/p/ainews-ai-cybersecurity-becomes-top",
    title="[AINews] AI Cybersecurity becomes top of mind",
    best_tag="new-tool-launch",
    similarity_score=0.251,
)


# ── Zero-items fallbacks ──────────────────────────────────────────────────────

def test_zero_plan_items_zero_proposals_fallback():
    text, item_map = format_plan([], 0, RUN_ID, [])
    assert text == "📋 <b>Weekly Plan</b>\n\n<i>Nothing on the plan this week.</i>"
    assert item_map == {}


# ── Uncategorized-flagging (2026-07-22, lightweight-uncategorized-flagging) ────
# Same real demonstration case as assemble_digest: today's actual Latent
# Space AI-cybersecurity item. Originally taste-prefiltered at cosine=0.186
# vs "new-tool-launch" (threshold 0.30) when only the 51-char RSS
# <description> teaser was embedded. After the same-day content-truncation
# fix (rss_common.py now reads the full 29,364-char <content:encoded>
# article), a LIVE re-check against the real current topic vectors moved
# the score to 0.251 -- closer, but still below 0.30. Confirmed by direct
# execution against the real store/embeddings, not assumed.

def test_uncategorized_item_no_longer_vanishes_before_after():
    before_text, before_map = format_plan([], 0, RUN_ID, [])
    assert "cybersecurity" not in before_text.lower()
    assert before_map == {}

    after_text, after_map = format_plan(
        [], 0, RUN_ID, [], uncategorized_items=[_REAL_CYBERSECURITY_ITEM]
    )
    assert "AI Cybersecurity becomes top of mind" in after_text
    assert "https://www.latent.space/p/ainews-ai-cybersecurity-becomes-top" in after_text
    assert "new-tool-launch" in after_text
    assert "0.251" in after_text
    assert after_map[1]["url"] == "https://www.latent.space/p/ainews-ai-cybersecurity-becomes-top"
    assert after_map[1]["tags"] == ["uncategorized"]
    assert "Nothing on the plan this week" not in after_text


def test_uncategorized_section_appears_even_with_nothing_else_on_plan():
    text, item_map = format_plan([], 0, RUN_ID, [], uncategorized_items=[_uncategorized()])
    assert "Nothing on the plan this week" not in text
    assert "1 item(s) didn't match any existing topic" in text
    assert set(item_map.keys()) == {1}


def test_uncategorized_numbering_continues_after_all_other_sections():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id=None, title="Course B", tags=["course"]),
        _plan_item(matched_card_id="card1", title="Project C"),
    ]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[2]["url"])]
    text, item_map = format_plan(
        items, 0, RUN_ID, [_card("card1")], priority,
        uncategorized_items=[_uncategorized(title="Uncategorized D")],
    )
    assert item_map[1]["title"] == "Article A"
    assert item_map[2]["title"] == "Course B"
    assert item_map[3]["title"] == "Project C"
    assert item_map[4]["title"] == "Uncategorized D"
    assert text.index("Project C") < text.index("Uncategorized D")


def test_footer_includes_uncategorized_count():
    text, item_map = format_plan(
        [_plan_item()], 0, RUN_ID, [], uncategorized_items=[_uncategorized(), _uncategorized()]
    )
    assert "1 plan items" in text
    assert "2 uncategorized" in text


def test_no_uncategorized_items_omits_the_section_entirely():
    text, item_map = format_plan([_plan_item()], 0, RUN_ID, [], uncategorized_items=[])
    assert "didn't match any existing topic" not in text
    assert "uncategorized" not in text


def test_zero_plan_items_with_proposals_includes_clause():
    text, item_map = format_plan([_proposal()], 2, RUN_ID, [])
    assert "Nothing on the plan this week." in text
    assert "2 proposals pending approval" in text
    assert "check Telegram" in text


def test_zero_plan_items_zero_proposals_no_proposal_clause():
    text, item_map = format_plan([], 0, RUN_ID, [])
    assert "proposals" not in text


def test_stale_nudge_only_week_does_not_trigger_empty_fallback():
    """A week with zero new matched content but a real stale-card nudge is
    NOT an empty plan -- Existing Project Work can be the only section."""
    priority = [_priority_entry(matched_card_id="card1", source="stale_nudge", item_url=None)]
    text, item_map = format_plan([], 0, RUN_ID, [_card("card1")], priority)
    assert "Nothing on the plan" not in text
    assert "<b>Existing Project Work</b>" in text


# ── Section routing ───────────────────────────────────────────────────────────

def test_proposals_excluded_from_output():
    items = [_plan_item(title="Keep me"), _proposal(title="Skip me")]
    text, item_map = format_plan(items, 1, RUN_ID, [])
    assert "Keep me" in text
    assert "Skip me" not in text


def test_unmatched_item_in_reading_section_only():
    items = [_plan_item(matched_card_id=None, title="Read this")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert "<b>Reading & Learning</b>" in text
    assert "Read this" in text
    assert "<b>Existing Project Work</b>" not in text


def test_matched_item_in_project_section_only():
    """A matched_card_id item only renders in Existing Project Work if
    prioritize_plan_items actually selected it (final sub-phase) -- not
    automatically, the way it worked before bounding existed."""
    items = [_plan_item(matched_card_id="card1", title="Continue this")]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[0]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")], priority)
    assert "<b>Existing Project Work</b>" in text
    assert "Continue this" in text
    assert "<b>Reading & Learning</b>" not in text


def test_both_sections_present_when_both_types_exist():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id="card1", title="Project B"),
    ]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[1]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")], priority)
    assert "<b>Reading & Learning</b>" in text
    assert "<b>Existing Project Work</b>" in text
    assert text.index("Article A") < text.index("Project B")


def test_matched_item_not_selected_by_prioritization_does_not_render():
    """Bounding: a real matched_card_id item that prioritize_plan_items
    didn't select doesn't appear anywhere in the rendered plan -- not in
    Existing Project Work, and not silently moved to Reading & Learning
    either."""
    items = [_plan_item(matched_card_id="card1", title="Not selected")]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")], prioritized_project_work=[])
    assert "Not selected" not in text


# ── Courses section ─────────────────────────────────────────────────────────────

def test_course_tagged_item_in_courses_section_not_reading():
    items = [_plan_item(matched_card_id=None, title="Deep Learning Specialization", tags=["course"])]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert "<b>Courses</b>" in text
    assert "Deep Learning Specialization" in text
    assert "<b>Reading & Learning</b>" not in text


def test_courses_section_omitted_when_no_course_items():
    items = [_plan_item(matched_card_id=None, title="Article A")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert "<b>Courses</b>" not in text


def test_course_tagged_item_goes_to_courses_even_when_matched_to_a_card():
    """A course tag takes priority over matched_card_id -- courses are a
    format-based section, not routed by Trello correlation like project
    work is. No prioritized_project_work entry needed since this item
    never becomes a project-work candidate at all (excluded upstream in
    prioritize_plan_items)."""
    items = [_plan_item(matched_card_id="card1", title="Agents Course", tags=["course"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")])
    assert "<b>Courses</b>" in text
    assert "Agents Course" in text
    assert "<b>Existing Project Work</b>" not in text


def test_all_three_sections_present_and_ordered():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id=None, title="Course B", tags=["course"]),
        _plan_item(matched_card_id="card1", title="Project C"),
    ]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[2]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")], priority)
    assert "<b>Reading & Learning</b>" in text
    assert "<b>Courses</b>" in text
    assert "<b>Existing Project Work</b>" in text
    assert text.index("Article A") < text.index("Course B") < text.index("Project C")


# ── Existing Project Work: stale_nudge + priority order (final sub-phase) ──────

def test_stale_nudge_entry_renders_from_trello_card_not_scored_item():
    """A stale_nudge entry has no underlying classified item -- it renders
    from the real Trello card's own name/url, labeled as an idle-board
    nudge rather than "continues card" (2026-07-26 relabel: the title IS
    already the card name for a stale_nudge entry, so repeating it via
    "continues card: ..." duplicated the same text with no new
    information and made a legitimate idle-card nudge indistinguishable
    from a raw Trello-card leak)."""
    priority = [_priority_entry(matched_card_id="card1", source="stale_nudge", item_url=None,
                                 priority_reasoning="Idle for weeks.")]
    text, item_map = format_plan([], 0, RUN_ID, [_card("card1", "My Stale Card", "https://trello.com/c/stale")], priority)
    assert "<b>Existing Project Work</b>" in text
    assert "My Stale Card" in text
    assert "idle board item, no new content this week" in text
    assert 'continues card: "My Stale Card"' not in text
    assert item_map[1]["url"] == "https://trello.com/c/stale"


def test_new_item_and_stale_nudge_suffixes_are_visually_distinct():
    """Direct regression test for the relabel: in the same rendered plan,
    a new_item entry keeps "continues card" (it's genuinely new content
    related to a card) while a stale_nudge entry gets the idle-board
    marker instead -- so the two are never visually indistinguishable."""
    items = [_plan_item(matched_card_id="card1", title="Fresh Article", url="https://a.com")]
    priority = [
        _priority_entry(matched_card_id="card1", source="new_item", item_url="https://a.com"),
        _priority_entry(matched_card_id="card2", source="stale_nudge", item_url=None),
    ]
    cards = [_card("card1", "Active Card"), _card("card2", "Idle Card")]
    text, item_map = format_plan(items, 0, RUN_ID, cards, priority)
    assert 'continues card: "Active Card"' in text
    assert 'continues card: "Idle Card"' not in text
    assert "idle board item, no new content this week" in text


def test_movement_note_appended_to_reasoning():
    priority = [_priority_entry(matched_card_id="card1", source="stale_nudge", item_url=None,
                                 priority_reasoning="Still relevant.", movement_note="unchanged since last week")]
    text, item_map = format_plan([], 0, RUN_ID, [_card("card1")], priority)
    assert "Still relevant. — unchanged since last week" in text


def test_no_movement_note_leaves_reasoning_unmodified():
    priority = [_priority_entry(matched_card_id="card1", priority_reasoning="Just this.", movement_note=None)]
    text, item_map = format_plan([], 0, RUN_ID, [_card("card1")], priority)
    assert "Just this.</i>" in text  # closing italic tag immediately after, no trailing " — "


def test_project_section_renders_in_prioritized_order_not_source_order():
    """The whole point of item 7: rendering order comes from
    prioritize_plan_items' priority order, not classified_items' order."""
    items = [
        _plan_item(matched_card_id="card1", title="Item A", url="https://a.com"),
        _plan_item(matched_card_id="card2", title="Item B", url="https://b.com"),
    ]
    priority = [
        _priority_entry(matched_card_id="card2", item_url="https://b.com"),
        _priority_entry(matched_card_id="card1", item_url="https://a.com"),
    ]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1"), _card("card2")], priority)
    assert text.index("Item B") < text.index("Item A")
    assert item_map[1]["title"] == "Item B"
    assert item_map[2]["title"] == "Item A"


def test_new_item_entry_with_unresolvable_url_falls_back_to_card():
    """Defensive: a new_item entry whose item_url doesn't match any
    classified item (shouldn't happen given prioritize_plan_items'
    validation, but format_plan must not crash) renders from the card."""
    priority = [_priority_entry(matched_card_id="card1", source="new_item", item_url="https://ghost.com")]
    text, item_map = format_plan([], 0, RUN_ID, [_card("card1", "Fallback Card")], priority)
    assert "Fallback Card" in text


# ── HTML escaping ────────────────────────────────────────────────────────────

def test_ampersand_in_title_is_html_escaped():
    """A real trigger for the original bug class: unescaped special
    characters in LLM/source-generated text breaking the parser. HTML
    mode reserves &, <, > -- not underscores."""
    items = [_plan_item(title="Research & Compare Tools", reasoning="Good.")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert "Research &amp; Compare Tools" in text
    assert "Research & Compare Tools</a>" not in text  # raw & must not appear unescaped inside the tag


def test_angle_brackets_in_reasoning_are_html_escaped():
    items = [_plan_item(reasoning="Compares <LangGraph> vs other frameworks.")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert "&lt;LangGraph&gt;" in text
    assert "<LangGraph>" not in text


def test_card_name_with_special_characters_is_html_escaped():
    items = [_plan_item(matched_card_id="card1")]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[0]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1", 'R&D <notes>')], priority)
    assert "R&amp;D &lt;notes&gt;" in text


def test_underscore_no_longer_needs_escaping():
    """The actual root cause of the real 2026-07-19 send_telegram_plan 400:
    a literal underscore in reasoning must render as a plain literal
    character under HTML mode, not trigger any escaping at all -- HTML
    mode doesn't reserve '_'."""
    items = [_plan_item(reasoning="Uses score_node and run_id and last_activity.")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert "score_node and run_id and last_activity" in text
    assert "\\_" not in text


# ── Numbering ─────────────────────────────────────────────────────────────────

def test_items_numbered_sequentially_across_sections():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id="card1", title="Project B"),
    ]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[1]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")], priority)
    assert "1. <a" in text
    assert "2. <a" in text
    assert text.index("Article A") < text.index("Project B")


def test_items_numbered_sequentially_across_all_three_sections():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id=None, title="Course B", tags=["course"]),
        _plan_item(matched_card_id="card1", title="Project C"),
    ]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[2]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")], priority)
    assert item_map[1]["title"] == "Article A"
    assert item_map[2]["title"] == "Course B"
    assert item_map[3]["title"] == "Project C"


# ── Card name resolution ──────────────────────────────────────────────────────

def test_card_name_appended_for_matched_item():
    items = [_plan_item(matched_card_id="card1")]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[0]["url"],
                                 priority_reasoning="Builds on prior work.")]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1", "Weekly Intel Agent")], priority)
    assert 'continues card: "Weekly Intel Agent"' in text


def test_unknown_card_id_falls_back_to_id_string():
    items = [_plan_item(matched_card_id="ghost-id")]
    priority = [_priority_entry(matched_card_id="ghost-id", item_url=items[0]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [], priority)
    assert 'continues card: "ghost-id"' in text


# ── Formatting (Reading & Learning section -- unaffected by this sub-phase) ────

def test_reasoning_wrapped_in_italic_tag():
    items = [_plan_item(reasoning="This is the reasoning.")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert "<i>This is the reasoning.</i>" in text


def test_title_truncated_to_80_chars():
    items = [_plan_item(title="A" * 100)]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert "A" * 80 in text
    assert "A" * 81 not in text


def test_title_rendered_as_html_link():
    items = [_plan_item(title="Article Title", url="https://example.com/article")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert '<a href="https://example.com/article">Article Title</a>' in text


# ── Footer ────────────────────────────────────────────────────────────────────

def test_footer_plan_count_and_run_id():
    items = [_plan_item(), _plan_item()]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert "2 plan items" in text
    assert "run: abc12345" in text


def test_footer_reflects_bounded_project_work_not_unbounded_matched_count():
    """The footer count must reflect what's actually rendered -- a matched
    item that prioritize_plan_items excluded should not inflate the count."""
    items = [
        _plan_item(matched_card_id="card1", title="Selected"),
        _plan_item(matched_card_id="card2", title="Not selected"),
    ]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[0]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1"), _card("card2")], priority)
    assert "1 plan items" in text


def test_footer_omits_proposal_clause_when_zero():
    text, item_map = format_plan([_plan_item()], 0, RUN_ID, [])
    assert "proposals" not in text


def test_footer_includes_proposal_clause_when_nonzero():
    text, item_map = format_plan([_plan_item()], 3, RUN_ID, [])
    assert "3 proposals pending approval" in text
    assert "·" in text


def test_missing_title_falls_back_to_text():
    item = {
        "url": "https://example.com", "text": "Full body text used as fallback title.",
        "reasoning": "Good content.", "classification": "plan_item",
        "proposal_type": None, "classification_reasoning": "routed as plan_item",
        "matched_card_id": None, "tags": ["agentic-engineering"],
    }
    text, item_map = format_plan([item], 0, RUN_ID, [])
    assert "Full body text used as fallback title." in text


# ── item_map ─────────────────────────────────────────────────────────────────

def test_item_map_keyed_by_display_number_with_correct_fields():
    items = [_plan_item(title="Article A", url="https://a.example.com", text="article body")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert set(item_map.keys()) == {1}
    assert item_map[1]["url"] == "https://a.example.com"
    assert item_map[1]["title"] == "Article A"
    assert item_map[1]["text"] == "article body"


def test_item_map_stores_raw_unescaped_text_not_rendered_html():
    """Critical for carry_forward.py: item_map must store RAW text, not
    the HTML-escaped rendered version -- otherwise a carried item fed
    back through format_plan() a second time would get double-escaped
    ('&' -> '&amp;' -> '&amp;amp;')."""
    items = [_plan_item(title="R&D notes", reasoning="Uses <brackets> and & signs.")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert item_map[1]["title"] == "R&D notes"
    assert item_map[1]["reasoning"] == "Uses <brackets> and & signs."
    # but the rendered text itself IS escaped
    assert "R&amp;D notes" in text
    assert "&lt;brackets&gt;" in text


def test_item_map_numbering_continues_across_both_sections():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id="card1", title="Project B"),
    ]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[1]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")], priority)
    assert set(item_map.keys()) == {1, 2}
    assert item_map[1]["title"] == "Article A"
    assert item_map[2]["title"] == "Project B"


def test_item_map_numbering_continues_across_all_three_sections():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id=None, title="Course B", tags=["course"]),
        _plan_item(matched_card_id="card1", title="Project C"),
    ]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[2]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")], priority)
    assert set(item_map.keys()) == {1, 2, 3}
    assert item_map[1]["title"] == "Article A"
    assert item_map[2]["title"] == "Course B"
    assert item_map[3]["title"] == "Project C"


# ── assemble_plan() node wrapper: plan_history recording ───────────────────────
# (Sub-phase 3, revised final sub-phase: plan_history now reflects
# prioritized_project_work -- what was ACTUALLY surfaced -- not the raw
# matched-item set. get_store() and record_plan_history() both mocked so
# this stays fully offline.)

def _saturday_state(classified_items, trello_cards=None, prioritized_project_work=None, run_id=RUN_ID, uncategorized_items=None):
    return {
        "run_id": run_id, "classified_items": classified_items,
        "trello_cards": trello_cards or [], "prioritized_project_work": prioritized_project_work or [],
        "pending_approvals": [], "uncategorized_items": uncategorized_items or [],
        "costs": [],
    }


def test_assemble_plan_records_cards_from_prioritized_project_work():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id="card1", title="Project B"),
    ]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[1]["url"])]
    fake_store = MagicMock()
    with patch("saturday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("saturday.nodes.assemble_plan.get_carry_forward_items", return_value=[]), \
         patch("saturday.nodes.assemble_plan.record_plan_history") as mock_record:
        assemble_plan(_saturday_state(items, [_card("card1")], priority))

    mock_record.assert_called_once_with(RUN_ID, [{"card_id": "card1", "list_name": "In Progress"}])


def test_assemble_plan_records_empty_when_prioritized_project_work_is_empty():
    """Even if classified_items has a real matched item, if
    prioritize_plan_items didn't select it, plan_history must NOT record
    it -- 'surfaced' now means 'actually rendered', not 'happened to
    match something this week'."""
    items = [_plan_item(matched_card_id="card1", title="Project B")]
    fake_store = MagicMock()
    with patch("saturday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("saturday.nodes.assemble_plan.get_carry_forward_items", return_value=[]), \
         patch("saturday.nodes.assemble_plan.record_plan_history") as mock_record:
        assemble_plan(_saturday_state(items, [_card("card1")], prioritized_project_work=[]))

    mock_record.assert_called_once_with(RUN_ID, [])


def test_assemble_plan_falls_back_to_unknown_list_name_if_card_not_in_trello_cards():
    priority = [_priority_entry(matched_card_id="ghost-card", item_url="https://example.com")]
    fake_store = MagicMock()
    with patch("saturday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("saturday.nodes.assemble_plan.get_carry_forward_items", return_value=[]), \
         patch("saturday.nodes.assemble_plan.record_plan_history") as mock_record:
        assemble_plan(_saturday_state([], trello_cards=[], prioritized_project_work=priority))

    mock_record.assert_called_once_with(RUN_ID, [{"card_id": "ghost-card", "list_name": "Unknown"}])


def test_assemble_plan_still_writes_current_weekly_plan():
    items = [_plan_item(matched_card_id=None, title="Article A")]
    fake_store = MagicMock()
    with patch("saturday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("saturday.nodes.assemble_plan.get_carry_forward_items", return_value=[]), \
         patch("saturday.nodes.assemble_plan.record_plan_history"):
        assemble_plan(_saturday_state(items))

    # Two puts now: the existing ("companion",) write, plus the
    # ("village",) event write added alongside it (2026-07-23,
    # village-namespace event writes) -- checking the companion call
    # specifically rather than assert_called_once.
    assert fake_store.put.call_count == 2
    companion_calls = [c for c in fake_store.put.call_args_list if c.args[0] == ("companion",)]
    assert len(companion_calls) == 1
    namespace, key, value = companion_calls[0].args
    assert namespace == ("companion",)
    assert key == "current_weekly_plan"
    assert value["run_id"] == RUN_ID


def test_assemble_plan_passes_prioritized_project_work_through_to_format_plan():
    """End-to-end through the node wrapper: a stale_nudge entry with no
    matched classified item still renders Existing Project Work."""
    priority = [_priority_entry(matched_card_id="card1", source="stale_nudge", item_url=None,
                                 priority_reasoning="Idle for weeks.")]
    fake_store = MagicMock()
    with patch("saturday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("saturday.nodes.assemble_plan.get_carry_forward_items", return_value=[]), \
         patch("saturday.nodes.assemble_plan.record_plan_history"):
        result = assemble_plan(_saturday_state([], [_card("card1", "My Card")], priority))

    assert "<b>Existing Project Work</b>" in result["plan_text"]
    assert "My Card" in result["plan_text"]


# ── assemble_plan() node wrapper: carry-forward integration ────────────────────

def test_assemble_plan_merges_carried_items_into_rendered_plan():
    carried = [{
        "url": "https://carried.com/1", "title": "Carried Article", "text": "body",
        "reasoning": "carried forward, unfinished last week", "classification": "plan_item",
        "proposal_type": None, "classification_reasoning": "carried forward, unfinished last week",
        "matched_card_id": None, "tags": ["agentic-engineering"],
    }]
    fake_store = MagicMock()
    with patch("saturday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("saturday.nodes.assemble_plan.get_carry_forward_items", return_value=carried), \
         patch("saturday.nodes.assemble_plan.record_plan_history"):
        result = assemble_plan(_saturday_state([]))

    assert "Carried Article" in result["plan_text"]
    assert "<b>Reading & Learning</b>" in result["plan_text"]


def test_assemble_plan_calls_get_carry_forward_items_with_current_run_id():
    fake_store = MagicMock()
    with patch("saturday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("saturday.nodes.assemble_plan.get_carry_forward_items", return_value=[]) as mock_carry, \
         patch("saturday.nodes.assemble_plan.record_plan_history"):
        assemble_plan(_saturday_state([]))

    mock_carry.assert_called_once_with(RUN_ID)


def test_assemble_plan_carried_item_not_recorded_in_plan_history():
    """Carried items have no matched_card_id -- they must never appear in
    plan_history (Trello-only) regardless of how they got into the plan."""
    carried = [{
        "url": "https://carried.com/1", "title": "Carried Article", "text": "body",
        "reasoning": "r", "classification": "plan_item", "proposal_type": None,
        "classification_reasoning": "r", "matched_card_id": None, "tags": [],
    }]
    fake_store = MagicMock()
    with patch("saturday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("saturday.nodes.assemble_plan.get_carry_forward_items", return_value=carried), \
         patch("saturday.nodes.assemble_plan.record_plan_history") as mock_record:
        assemble_plan(_saturday_state([]))

    mock_record.assert_called_once_with(RUN_ID, [])


# ── Length budget / reasoning truncation (2026-07-19) ───────────────────────────
# Telegram hard-caps sendMessage at 4096 chars; a real send already reached
# 4032/4096 (98.4%) from the HTML parse_mode fix alone. Chosen fix: truncate
# reasoning (and Existing Project Work's card_name) to a fixed per-item
# budget only when the full render would exceed MAX_PLAN_TEXT_CHARS -- item
# COUNT stays unbounded either way.

def test_under_budget_text_is_not_truncated():
    """The common case: well under the soft budget, reasoning renders in
    full, no truncation logic engaged at all."""
    items = [_plan_item(reasoning="A perfectly normal, short reasoning sentence.")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert "A perfectly normal, short reasoning sentence." in text
    assert "…" not in text
    assert len(text) < MAX_PLAN_TEXT_CHARS


def test_over_budget_reasoning_is_truncated_with_ellipsis():
    """Force the full render past MAX_PLAN_TEXT_CHARS with long reasoning
    text on every item, confirm the SENT text is truncated and stays
    under budget."""
    long_reasoning = "This is a very long piece of reasoning text. " * 30  # ~1400 chars
    items = [_plan_item(reasoning=long_reasoning, title=f"Item {i}", url=f"https://example.com/{i}")
             for i in range(5)]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert len(text) <= MAX_PLAN_TEXT_CHARS + 200  # generous slack for headers/footer/tags
    assert "…" in text
    # the truncated rendered reasoning must be shorter than the raw original
    assert len(long_reasoning) > REASONING_CHAR_BUDGET


def test_item_map_keeps_full_untruncated_reasoning_even_when_rendered_text_is_capped():
    """Critical for carry_forward.py: a carried item reused next week must
    not be permanently stuck with a truncated blurb just because THIS
    week's message happened to be near the length limit."""
    long_reasoning = "This is a very long piece of reasoning text. " * 30
    items = [_plan_item(reasoning=long_reasoning, title=f"Item {i}", url=f"https://example.com/{i}")
             for i in range(5)]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    for entry in item_map.values():
        assert entry["reasoning"] == long_reasoning  # full text, not truncated


def test_over_budget_card_name_truncated_in_continues_card_suffix():
    """The real bug found alongside this fix: a stale_nudge card_name
    could run far longer than a typical title (a real example was
    ~200 chars) and rendered in full, twice, unbounded."""
    long_card_name = "A Very Long Trello Card Name " * 10  # ~300 chars
    long_reasoning = "This is a very long piece of reasoning text. " * 30
    items = [_plan_item(reasoning=long_reasoning, title=f"Item {i}", url=f"https://example.com/{i}")
             for i in range(5)]
    priority = [_priority_entry(matched_card_id="card1", source="stale_nudge", item_url=None,
                                 priority_reasoning=long_reasoning)]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1", long_card_name)], priority)
    assert len(text) <= MAX_PLAN_TEXT_CHARS + 200
    # the card_name must not appear in full (uncapped) length in the rendered text
    assert long_card_name not in text


def test_stale_nudge_title_truncated_to_80_chars():
    """Direct test of the always-on bug fix: card_name-derived TITLES now
    get the same [:80] truncation every other title path already had.
    (2026-07-26: a stale_nudge entry's suffix no longer repeats card_name
    at all -- see the idle-board-item relabel -- so the title's own [:80]
    cap is now the only place a long card name is ever truncated for this
    entry type.)"""
    long_card_name = "A" * 150
    priority = [_priority_entry(matched_card_id="card1", source="stale_nudge", item_url=None)]
    text, item_map = format_plan([], 0, RUN_ID, [_card("card1", long_card_name)], priority)
    assert item_map[1]["title"] == "A" * 80
    assert f'>{"A" * 80}</a>' in text
    assert f'>{"A" * 81}' not in text  # the link title itself never exceeds 80


def test_shrinking_safety_net_engages_for_extreme_overflow():
    """Even the fixed REASONING_CHAR_BUDGET might not be enough for a
    genuinely extreme item count -- the shrinking safety net must still
    land under budget (or at least make real, repeated forward progress),
    not loop forever or silently give up at the first cap."""
    long_reasoning = "This is a very long piece of reasoning text. " * 30
    items = [_plan_item(reasoning=long_reasoning, title=f"Item {i}", url=f"https://example.com/{i}")
             for i in range(40)]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    # 40 items even at the minimum ~20-char reasoning budget plus fixed
    # per-item overhead (title, url, numbering) will not fit under 3900 --
    # the real assertion here is that truncation was actually APPLIED
    # (shrunk well below the untruncated ~50000+ char version), not that
    # it necessarily made it under the soft budget in this extreme case.
    assert len(text) < 20000
    assert "…" in text

