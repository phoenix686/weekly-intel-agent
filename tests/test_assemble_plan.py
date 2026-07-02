import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sunday.nodes.assemble_plan import format_plan

RUN_ID = "abc12345-0000-0000-0000-000000000000"


def _plan_item(matched_card_id=None, title="A title", reasoning="Good content.",
               url="https://example.com"):
    return {
        "url": url, "title": title, "reasoning": reasoning,
        "classification": "plan_item", "proposal_type": None,
        "classification_reasoning": "routed as plan_item",
        "matched_card_id": matched_card_id, "tags": ["agentic-engineering"],
    }


def _proposal(title="New project idea"):
    return {
        "url": "https://example.com/proposal", "title": title,
        "reasoning": "Interesting new direction.", "classification": "project_proposal",
        "proposal_type": "new", "classification_reasoning": "new scope",
        "matched_card_id": None, "tags": ["side-projects"],
    }


def _card(card_id="card1", name="My Trello Card"):
    return {"card_id": card_id, "name": name, "list_name": "In Progress"}


# ── Zero-items fallbacks ──────────────────────────────────────────────────────

def test_zero_plan_items_zero_proposals_fallback():
    result = format_plan([], 0, RUN_ID, [])
    assert result == "📋 *Weekly Plan*\n\n_Nothing on the plan this week._"


def test_zero_plan_items_with_proposals_includes_clause():
    result = format_plan([_proposal()], 2, RUN_ID, [])
    assert "Nothing on the plan this week." in result
    assert "2 proposals pending approval" in result
    assert "check Telegram" in result


def test_zero_plan_items_zero_proposals_no_proposal_clause():
    result = format_plan([], 0, RUN_ID, [])
    assert "proposals" not in result


# ── Section routing ───────────────────────────────────────────────────────────

def test_proposals_excluded_from_output():
    items = [_plan_item(title="Keep me"), _proposal(title="Skip me")]
    result = format_plan(items, 1, RUN_ID, [])
    assert "Keep me" in result
    assert "Skip me" not in result


def test_unmatched_item_in_reading_section_only():
    items = [_plan_item(matched_card_id=None, title="Read this")]
    result = format_plan(items, 0, RUN_ID, [])
    assert "**Reading & Learning**" in result
    assert "Read this" in result
    assert "**Existing Project Work**" not in result


def test_matched_item_in_project_section_only():
    items = [_plan_item(matched_card_id="card1", title="Continue this")]
    result = format_plan(items, 0, RUN_ID, [_card("card1")])
    assert "**Existing Project Work**" in result
    assert "Continue this" in result
    assert "**Reading & Learning**" not in result


def test_both_sections_present_when_both_types_exist():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id="card1", title="Project B"),
    ]
    result = format_plan(items, 0, RUN_ID, [_card("card1")])
    assert "**Reading & Learning**" in result
    assert "**Existing Project Work**" in result
    assert result.index("Article A") < result.index("Project B")


# ── Numbering ─────────────────────────────────────────────────────────────────

def test_items_numbered_sequentially_across_sections():
    items = [
        _plan_item(matched_card_id=None, title="Article A"),
        _plan_item(matched_card_id="card1", title="Project B"),
    ]
    result = format_plan(items, 0, RUN_ID, [_card("card1")])
    assert "1. [Article A]" in result
    assert "2. [Project B]" in result


# ── Card name resolution ──────────────────────────────────────────────────────

def test_card_name_appended_for_matched_item():
    items = [_plan_item(matched_card_id="card1", reasoning="Builds on prior work.")]
    result = format_plan(items, 0, RUN_ID, [_card("card1", "Weekly Intel Agent")])
    assert 'continues card: "Weekly Intel Agent"' in result


def test_unknown_card_id_falls_back_to_id_string():
    items = [_plan_item(matched_card_id="ghost-id")]
    result = format_plan(items, 0, RUN_ID, [])
    assert 'continues card: "ghost-id"' in result


# ── Formatting ────────────────────────────────────────────────────────────────

def test_underscore_escaping_in_reasoning():
    items = [_plan_item(reasoning="Uses score_node and run_id.")]
    result = format_plan(items, 0, RUN_ID, [])
    assert r"score\_node" in result
    assert r"run\_id" in result


def test_reasoning_wrapped_in_italic():
    items = [_plan_item(reasoning="This is the reasoning.")]
    result = format_plan(items, 0, RUN_ID, [])
    assert "_This is the reasoning._" in result


def test_title_truncated_to_80_chars():
    items = [_plan_item(title="A" * 100)]
    result = format_plan(items, 0, RUN_ID, [])
    assert "A" * 80 in result
    assert "A" * 81 not in result


# ── Footer ────────────────────────────────────────────────────────────────────

def test_footer_plan_count_and_run_id():
    items = [_plan_item(), _plan_item()]
    result = format_plan(items, 0, RUN_ID, [])
    assert "2 plan items" in result
    assert "run: abc12345" in result


def test_footer_omits_proposal_clause_when_zero():
    result = format_plan([_plan_item()], 0, RUN_ID, [])
    assert "proposals" not in result


def test_footer_includes_proposal_clause_when_nonzero():
    result = format_plan([_plan_item()], 3, RUN_ID, [])
    assert "3 proposals pending approval" in result
    assert "·" in result


def test_missing_title_falls_back_to_text():
    item = {
        "url": "https://example.com", "text": "Full body text used as fallback title.",
        "reasoning": "Good content.", "classification": "plan_item",
        "proposal_type": None, "classification_reasoning": "routed as plan_item",
        "matched_card_id": None, "tags": ["agentic-engineering"],
    }
    result = format_plan([item], 0, RUN_ID, [])
    assert "Full body text used as fallback title." in result
