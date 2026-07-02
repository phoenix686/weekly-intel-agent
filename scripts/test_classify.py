"""
Expected Output:
Fetched 32 Trello cards
Correlated 2 items

https://twitter.com/Qihong53030163/status/2071089857489928620 | plan_item | None | Standalone learning resource (Twitter thread on LLM architecture/optimization). No existing project scope expansion—this is routine reading material for technical development.
https://twitter.com/cwolferesearch/status/2070942465654186054 | plan_item | None | Standalone technical learning resource on evaluation design and benchmarking. No matched card and no indication of new project scope—routine reference material for existing agent/model work.

Summary: 2 plan_items, 0 project_proposals
"""
import json
import os
import sys
from pathlib import Path

from sunday.nodes.read_trello import read_trello
from sunday.nodes.correlate_trello import correlate_trello

from sunday.nodes.classify_item import classify_item
from state import make_sunday_initial_state

SCORED_ITEMS_PATH = Path("data/scored_items.json")
TEST_FIXTURE_PATH = "data/test_fixture.json"
TEST_LIMIT = 8

if not SCORED_ITEMS_PATH.exists():
    print(
        f"ERROR: {SCORED_ITEMS_PATH} not found.\n"
        "Run these first to generate it:\n"
        "  uv run --env-file .env python scripts/save_clustered.py\n"
        "  uv run --env-file .env python discovery/nodes/score.py"
    )
    sys.exit(1)

scored_items = json.loads(SCORED_ITEMS_PATH.read_text(encoding="utf-8"))

if os.environ.get("TWILLOT_JSON_PATH", "") == TEST_FIXTURE_PATH:
    scored_items = scored_items[:TEST_LIMIT]
    print(f"Test mode: sliced to {len(scored_items)} items (TWILLOT_JSON_PATH={TEST_FIXTURE_PATH})")
else:
    print(f"Loaded {len(scored_items)} scored items from {SCORED_ITEMS_PATH}")

state = make_sunday_initial_state(run_id="test-classify-1")
state["scored_items"] = scored_items

state.update(read_trello(state))
print(f"Fetched {len(state['trello_cards'])} Trello cards")

state.update(correlate_trello(state))
print(f"Correlated {len(state['correlated_items'])} items")

result = classify_item(state)
print()
for item in result["classified_items"]:
    print(item["url"], "|", item["classification"], "|", item.get("proposal_type"), "|", item["classification_reasoning"])

plan_count = sum(1 for i in result["classified_items"] if i["classification"] == "plan_item")
proposal_count = len(result["pending_approvals"])
print(f"\nSummary: {plan_count} plan_items, {proposal_count} project_proposals")
