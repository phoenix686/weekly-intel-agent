import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

from sunday.nodes.assemble_plan import format_plan, assemble_plan

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


# ── Zero-items fallbacks ──────────────────────────────────────────────────────

def test_zero_plan_items_zero_proposals_fallback():
    text, item_map = format_plan([], 0, RUN_ID, [])
    assert text == "📋 *Weekly Plan*\n\n_Nothing on the plan this week._"
    assert item_map == {}


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
    assert "**Existing Project Work**" in text


# ── Section routing ───────────────────────────────────────────────────────────

def test_proposals_excluded_from_output():
    items = [_plan_item(title="Keep me"), _proposal(title="Skip me")]
    text, item_map = format_plan(items, 1, RUN_ID, [])
    assert "Keep me" in text
    assert "Skip me" not in text


def test_unmatched_item_in_reading_section_only():
    items = [_plan_item(matched_card_id=None, title="Read this")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert "**Reading & Learning**" in text
    assert "Read this" in text
    assert "**Existing Project Work**" not in text


def test_matched_item_in_project_section_only():
    """A matched_card_id item only renders in Existing Project Work if
    prioritize_plan_items actually selected it (final sub-phase) -- not
    automatically, the way it worked before bounding existed."""
    items = [_plan_item(matched_card_id="card1", title="Continue this")]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[0]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")], priority)
    assert "**Existing Project Work**" in text
    assert "Continue this" in text
    assert "**Reading & Learning**" not in text


def test_both_sections_present_when_both_types_exist():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id="card1", title="Project B"),
    ]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[1]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")], priority)
    assert "**Reading & Learning**" in text
    assert "**Existing Project Work**" in text
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
    assert "**Courses**" in text
    assert "Deep Learning Specialization" in text
    assert "**Reading & Learning**" not in text


def test_courses_section_omitted_when_no_course_items():
    items = [_plan_item(matched_card_id=None, title="Article A")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert "**Courses**" not in text


def test_course_tagged_item_goes_to_courses_even_when_matched_to_a_card():
    """A course tag takes priority over matched_card_id -- courses are a
    format-based section, not routed by Trello correlation like project
    work is. No prioritized_project_work entry needed since this item
    never becomes a project-work candidate at all (excluded upstream in
    prioritize_plan_items)."""
    items = [_plan_item(matched_card_id="card1", title="Agents Course", tags=["course"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")])
    assert "**Courses**" in text
    assert "Agents Course" in text
    assert "**Existing Project Work**" not in text


def test_all_three_sections_present_and_ordered():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id=None, title="Course B", tags=["course"]),
        _plan_item(matched_card_id="card1", title="Project C"),
    ]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[2]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")], priority)
    assert "**Reading & Learning**" in text
    assert "**Courses**" in text
    assert "**Existing Project Work**" in text
    assert text.index("Article A") < text.index("Course B") < text.index("Project C")


# ── Existing Project Work: stale_nudge + priority order (final sub-phase) ──────

def test_stale_nudge_entry_renders_from_trello_card_not_scored_item():
    """A stale_nudge entry has no underlying classified item -- it renders
    from the real Trello card's own name/url."""
    priority = [_priority_entry(matched_card_id="card1", source="stale_nudge", item_url=None,
                                 priority_reasoning="Idle for weeks.")]
    text, item_map = format_plan([], 0, RUN_ID, [_card("card1", "My Stale Card", "https://trello.com/c/stale")], priority)
    assert "**Existing Project Work**" in text
    assert "My Stale Card" in text
    assert 'continues card: "My Stale Card"' in text
    assert item_map[1]["url"] == "https://trello.com/c/stale"


def test_movement_note_appended_to_reasoning():
    priority = [_priority_entry(matched_card_id="card1", source="stale_nudge", item_url=None,
                                 priority_reasoning="Still relevant.", movement_note="unchanged since last week")]
    text, item_map = format_plan([], 0, RUN_ID, [_card("card1")], priority)
    assert "Still relevant. — unchanged since last week" in text


def test_no_movement_note_leaves_reasoning_unmodified():
    priority = [_priority_entry(matched_card_id="card1", priority_reasoning="Just this.", movement_note=None)]
    text, item_map = format_plan([], 0, RUN_ID, [_card("card1")], priority)
    assert "Just this._" in text  # closing italic immediately after, no trailing " — "


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


# ── Numbering ─────────────────────────────────────────────────────────────────

def test_items_numbered_sequentially_across_sections():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id="card1", title="Project B"),
    ]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[1]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")], priority)
    assert "1. [Article A]" in text
    assert "2. [Project B]" in text


def test_items_numbered_sequentially_across_all_three_sections():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id=None, title="Course B", tags=["course"]),
        _plan_item(matched_card_id="card1", title="Project C"),
    ]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[2]["url"])]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")], priority)
    assert "1. [Article A]" in text
    assert "2. [Course B]" in text
    assert "3. [Project C]" in text


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

def test_underscore_escaping_in_reasoning():
    items = [_plan_item(reasoning="Uses score_node and run_id.")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert r"score\_node" in text
    assert r"run\_id" in text


def test_reasoning_wrapped_in_italic():
    items = [_plan_item(reasoning="This is the reasoning.")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert "_This is the reasoning._" in text


def test_title_truncated_to_80_chars():
    items = [_plan_item(title="A" * 100)]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert "A" * 80 in text
    assert "A" * 81 not in text


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

def _sunday_state(classified_items, trello_cards=None, prioritized_project_work=None, run_id=RUN_ID):
    return {
        "run_id": run_id, "classified_items": classified_items,
        "trello_cards": trello_cards or [], "prioritized_project_work": prioritized_project_work or [],
        "pending_approvals": [],
    }


def test_assemble_plan_records_cards_from_prioritized_project_work():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id="card1", title="Project B"),
    ]
    priority = [_priority_entry(matched_card_id="card1", item_url=items[1]["url"])]
    fake_store = MagicMock()
    with patch("sunday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("sunday.nodes.assemble_plan.record_plan_history") as mock_record:
        assemble_plan(_sunday_state(items, [_card("card1")], priority))

    mock_record.assert_called_once_with(RUN_ID, [{"card_id": "card1", "list_name": "In Progress"}])


def test_assemble_plan_records_empty_when_prioritized_project_work_is_empty():
    """Even if classified_items has a real matched item, if
    prioritize_plan_items didn't select it, plan_history must NOT record
    it -- 'surfaced' now means 'actually rendered', not 'happened to
    match something this week'."""
    items = [_plan_item(matched_card_id="card1", title="Project B")]
    fake_store = MagicMock()
    with patch("sunday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("sunday.nodes.assemble_plan.record_plan_history") as mock_record:
        assemble_plan(_sunday_state(items, [_card("card1")], prioritized_project_work=[]))

    mock_record.assert_called_once_with(RUN_ID, [])


def test_assemble_plan_falls_back_to_unknown_list_name_if_card_not_in_trello_cards():
    priority = [_priority_entry(matched_card_id="ghost-card", item_url="https://example.com")]
    fake_store = MagicMock()
    with patch("sunday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("sunday.nodes.assemble_plan.record_plan_history") as mock_record:
        assemble_plan(_sunday_state([], trello_cards=[], prioritized_project_work=priority))

    mock_record.assert_called_once_with(RUN_ID, [{"card_id": "ghost-card", "list_name": "Unknown"}])


def test_assemble_plan_still_writes_current_weekly_plan():
    items = [_plan_item(matched_card_id=None, title="Article A")]
    fake_store = MagicMock()
    with patch("sunday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("sunday.nodes.assemble_plan.record_plan_history"):
        assemble_plan(_sunday_state(items))

    fake_store.put.assert_called_once()
    namespace, key, value = fake_store.put.call_args[0]
    assert namespace == ("companion",)
    assert key == "current_weekly_plan"
    assert value["run_id"] == RUN_ID


def test_assemble_plan_passes_prioritized_project_work_through_to_format_plan():
    """End-to-end through the node wrapper: a stale_nudge entry with no
    matched classified item still renders Existing Project Work."""
    priority = [_priority_entry(matched_card_id="card1", source="stale_nudge", item_url=None,
                                 priority_reasoning="Idle for weeks.")]
    fake_store = MagicMock()
    with patch("sunday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("sunday.nodes.assemble_plan.record_plan_history"):
        result = assemble_plan(_sunday_state([], [_card("card1", "My Card")], priority))

    assert "**Existing Project Work**" in result["plan_text"]
    assert "My Card" in result["plan_text"]
