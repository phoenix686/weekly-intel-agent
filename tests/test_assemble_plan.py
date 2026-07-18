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


def _card(card_id="card1", name="My Trello Card"):
    return {"card_id": card_id, "name": name, "list_name": "In Progress"}


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
    items = [_plan_item(matched_card_id="card1", title="Continue this")]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")])
    assert "**Existing Project Work**" in text
    assert "Continue this" in text
    assert "**Reading & Learning**" not in text


def test_both_sections_present_when_both_types_exist():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id="card1", title="Project B"),
    ]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")])
    assert "**Reading & Learning**" in text
    assert "**Existing Project Work**" in text
    assert text.index("Article A") < text.index("Project B")


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
    work is."""
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
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")])
    assert "**Reading & Learning**" in text
    assert "**Courses**" in text
    assert "**Existing Project Work**" in text
    assert text.index("Article A") < text.index("Course B") < text.index("Project C")


# ── Numbering ─────────────────────────────────────────────────────────────────

def test_items_numbered_sequentially_across_sections():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id="card1", title="Project B"),
    ]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")])
    assert "1. [Article A]" in text
    assert "2. [Project B]" in text


def test_items_numbered_sequentially_across_all_three_sections():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id=None, title="Course B", tags=["course"]),
        _plan_item(matched_card_id="card1", title="Project C"),
    ]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")])
    assert "1. [Article A]" in text
    assert "2. [Course B]" in text
    assert "3. [Project C]" in text


# ── Card name resolution ──────────────────────────────────────────────────────

def test_card_name_appended_for_matched_item():
    items = [_plan_item(matched_card_id="card1", reasoning="Builds on prior work.")]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1", "Weekly Intel Agent")])
    assert 'continues card: "Weekly Intel Agent"' in text


def test_unknown_card_id_falls_back_to_id_string():
    items = [_plan_item(matched_card_id="ghost-id")]
    text, item_map = format_plan(items, 0, RUN_ID, [])
    assert 'continues card: "ghost-id"' in text


# ── Formatting ────────────────────────────────────────────────────────────────

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


# ── item_map (untested before this fix -- these tests never exercised the
# second half of the tuple return, even before it broke) ──────────────────────

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
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")])
    assert set(item_map.keys()) == {1, 2}
    assert item_map[1]["title"] == "Article A"
    assert item_map[2]["title"] == "Project B"


def test_item_map_numbering_continues_across_all_three_sections():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id=None, title="Course B", tags=["course"]),
        _plan_item(matched_card_id="card1", title="Project C"),
    ]
    text, item_map = format_plan(items, 0, RUN_ID, [_card("card1")])
    assert set(item_map.keys()) == {1, 2, 3}
    assert item_map[1]["title"] == "Article A"
    assert item_map[2]["title"] == "Course B"
    assert item_map[3]["title"] == "Project C"


# ── assemble_plan() node wrapper: plan_history recording ───────────────────────
# (Sunday plan LLM prioritization checkpoint, sub-phase 3 -- get_store() and
# record_plan_history() both mocked so this stays fully offline.)

def _sunday_state(classified_items, trello_cards=None, run_id=RUN_ID):
    return {
        "run_id": run_id, "classified_items": classified_items,
        "trello_cards": trello_cards or [], "pending_approvals": [],
    }


def test_assemble_plan_records_only_project_work_cards():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id="card1", title="Project B"),
    ]
    fake_store = MagicMock()
    with patch("sunday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("sunday.nodes.assemble_plan.record_plan_history") as mock_record:
        assemble_plan(_sunday_state(items, [_card("card1")]))

    mock_record.assert_called_once_with(RUN_ID, [{"card_id": "card1", "list_name": "In Progress"}])


def test_assemble_plan_excludes_course_tagged_cards_even_when_matched():
    """A course-tagged item that happens to have a matched_card_id renders
    in the Courses section, not Existing Project Work (sub-phase 1) -- its
    card should not be recorded in plan_history either, same rule."""
    items = [_plan_item(matched_card_id="card1", title="Agents Course", tags=["course"])]
    fake_store = MagicMock()
    with patch("sunday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("sunday.nodes.assemble_plan.record_plan_history") as mock_record:
        assemble_plan(_sunday_state(items, [_card("card1")]))

    mock_record.assert_called_once_with(RUN_ID, [])


def test_assemble_plan_excludes_proposals_from_plan_history():
    items = [_plan_item(matched_card_id="card1", title="Project B"), _proposal()]
    fake_store = MagicMock()
    with patch("sunday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("sunday.nodes.assemble_plan.record_plan_history") as mock_record:
        assemble_plan(_sunday_state(items, [_card("card1")]))

    mock_record.assert_called_once_with(RUN_ID, [{"card_id": "card1", "list_name": "In Progress"}])


def test_assemble_plan_records_empty_list_when_no_project_work():
    items = [_plan_item(matched_card_id=None, title="Article A")]
    fake_store = MagicMock()
    with patch("sunday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("sunday.nodes.assemble_plan.record_plan_history") as mock_record:
        assemble_plan(_sunday_state(items))

    mock_record.assert_called_once_with(RUN_ID, [])


def test_assemble_plan_falls_back_to_unknown_list_name_if_card_not_in_trello_cards():
    """Defensive: matched_card_id should always resolve against
    state["trello_cards"], but if it somehow doesn't, record a real
    placeholder instead of crashing or silently dropping the card."""
    items = [_plan_item(matched_card_id="ghost-card", title="Project B")]
    fake_store = MagicMock()
    with patch("sunday.nodes.assemble_plan.get_store", return_value=fake_store), \
         patch("sunday.nodes.assemble_plan.record_plan_history") as mock_record:
        assemble_plan(_sunday_state(items, trello_cards=[]))

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
