"""
Expected Output:
Fetched 32 Trello cards
Correlated 10 items

5 plan_items, 5 proposals

https://twitter.com/synthetic/status/1000000000000000001 | plan_item | None | Matched to existing card (6a3b3768dce0aa08d46965ca: digital simulated world agent concept). This is 
https://twitter.com/synthetic/status/1000000000000000002 | project_proposal | new | No matched_card_id. Score reasoning explicitly notes it overlaps with 'existing tracing/observabilit
https://twitter.com/synthetic/status/1000000000000000003 | project_proposal | new | Score reasoning explicitly calls this 'a full standalone project concept (multi-source personal know
https://twitter.com/synthetic/status/1000000000000000004 | plan_item | None | Described as 'foundational conceptual resource, directly useful for ongoing memory-systems work, sta
https://twitter.com/synthetic/status/1000000000000000005 | project_proposal | new | Score reasoning: 'Concrete new-feature concept...spaced-repetition flashcard generation—a genuine sc
https://twitter.com/synthetic/status/1000000000000000006 | plan_item | None | Matched to existing card (6a4355aa1948685d2d428377: Prompt caching deep agents). This is a directly 
https://twitter.com/synthetic/status/1000000000000000007 | project_proposal | new | Score reasoning: 'describes a distinct new tool (browser extension + webhook sync)' adjacent to but 
https://twitter.com/synthetic/status/1000000000000000008 | plan_item | None | Matched to existing card (6a3b371703c9bd2f1a78919a: voice agent / LangGraph agent concept). Score de
https://twitter.com/synthetic/status/1000000000000000009 | project_proposal | new | Score reasoning: 'tooling that could meaningfully extend the existing eval-building work...worth con
https://twitter.com/synthetic/status/1000000000000000010 | plan_item | None | Score describes this as 'practical, directly useful prompt-design reading, no project-scope implicat

==================================================
📋 *Weekly Plan*

**Reading & Learning**
1. [Solid explainer on the differences between RAG, long-context windows, and memory](https://twitter.com/synthetic/status/1000000000000000004)
   _Foundational conceptual resource, directly useful for ongoing memory-systems work, standard reading material._

2. [Long-form post on designing effective system prompts for coding agents, with bef](https://twitter.com/synthetic/status/1000000000000000010)
   _Practical, directly useful prompt-design reading, no project-scope implications._

**Existing Project Work**
3. [Great overview of gradient checkpointing techniques for training large models on](https://twitter.com/synthetic/status/1000000000000000001)
   _Directly relevant technical resource on memory-efficient training â€” useful background for understanding model constraints in agentic systems._ — continues card: "Upload latest calc on model memory gpu thingy into bible"

4. [Detailed writeup comparing DSPy, GEPA, and manual prompt engineering across thre](https://twitter.com/synthetic/status/1000000000000000006)
   _Directly applicable to planned prompt-optimization work, high-quality comparative resource._ — continues card: "Prompt caching deep agents"

5. [Short case study on reducing LangGraph recursion-limit failures in production by](https://twitter.com/synthetic/status/1000000000000000008)
   _Directly relevant to existing LangGraph agent reliability practices, standard technical reading._ — continues card: "Voice agent cam now be vreated by langgraph apparently there's an article i was thinking can this be built as an extension of the trellis agent that gives ideas and plan for the week and an insta reel I saved where a character floats on the screen and has a plan shown and can maybe speak"

_5 plan items · 5 proposals pending approval · run: test-syn_
"""
import json
from core.state import make_saturday_initial_state
from saturday.nodes.read_trello import read_trello
from saturday.nodes.correlate_trello import correlate_trello
from saturday.nodes.classify_item import classify_item

with open("data/scored_items_synthetic.json") as f:
    synthetic_items = json.load(f)

state = make_saturday_initial_state(run_id="test-synthetic-1")
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
from saturday.nodes.assemble_plan import assemble_plan

state.update(result)  # result is classify_item's output from before
plan_result = assemble_plan(state)
print("\n" + "="*50)
print(plan_result["plan_text"])