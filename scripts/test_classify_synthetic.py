import json
from state import make_sunday_initial_state
from sunday.nodes.read_trello import read_trello
from sunday.nodes.correlate_trello import correlate_trello
from sunday.nodes.classify_item import classify_item

with open("data/scored_items_synthetic.json") as f:
    synthetic_items = json.load(f)

state = make_sunday_initial_state(run_id="test-synthetic-1")
state["scored_items"] = synthetic_items

state.update(read_trello(state))
print(f"Fetched {len(state['trello_cards'])} Trello cards")

state.update(correlate_trello(state))
print(f"Correlated {len(state['correlated_items'])} items")

result = classify_item(state)

plan_items = [i for i in result["classified_items"] if i["classification"] == "plan_item"]
proposals = [i for i in result["classified_items"] if i["classification"] == "project_proposal"]

print(f"\n{len(plan_items)} plan_items, {len(proposals)} proposals\n")

for item in result["classified_items"]:
    print(f"{item['item_id']} | {item['classification']} | {item.get('proposal_type')} | {item['classification_reasoning'][:100]}")

# add to the bottom of scripts/test_classify_synthetic.py
from sunday.nodes.assemble_plan import assemble_plan

state.update(result)  # result is classify_item's output from before
plan_result = assemble_plan(state)
print("\n" + "="*50)
print(plan_result["plan_text"])