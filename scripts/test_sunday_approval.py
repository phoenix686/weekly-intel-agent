"""
Single end-to-end test: synthetic proposals -> real await_approval ->
real pause -> (you approve via Telegram or manually) -> real resume ->
real write_outputs.

Run once to trigger the pause. Run again (same file) to resume — the
script detects an existing paused thread and skips straight to the
resume step instead of re-triggering everything from scratch.

Run: uv run --env-file .env python scripts/test_sunday_approval.py
"""

import json
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send, Command
from checkpointer_config import get_checkpointer
from sunday.nodes.await_approval import route_to_approvals, proposal_worker
from state import make_sunday_initial_state
from sunday.nodes.read_trello import read_trello
from sunday.nodes.correlate_trello import correlate_trello
from sunday.nodes.classify_item import classify_item

THREAD_ID = "test-approval-run-1"
config = {"configurable": {"thread_id": THREAD_ID}}

approval_graph = StateGraph(dict)
approval_graph.add_node("proposal_worker", proposal_worker)
approval_graph.add_conditional_edges(
    START, lambda s: route_to_approvals({"pending_approvals": s["pending_approvals"]})
)
approval_graph.add_edge("proposal_worker", END)
compiled = approval_graph.compile(checkpointer=get_checkpointer())

existing_state = compiled.get_state(config)

if existing_state.next:
    # Already paused from a previous run of this script — go straight to resume
    print(f"Found existing paused thread '{THREAD_ID}'.")
    interrupts = existing_state.tasks[0].interrupts if existing_state.tasks else []
    print(f"Pending interrupts: {[(i.id, i.value) for i in interrupts]}")

    interrupt_id = input("\nPaste an interrupt id to resume: ").strip()
    decision = input("Decision (approve/reject): ").strip().lower()

    final = compiled.invoke(Command(resume={interrupt_id: decision}), config=config)
    print("\n=== RESUMED ===")
    print(final)

else:
    # Fresh run — build proposals from synthetic data and trigger the pause
    with open("data/scored_items_synthetic.json", encoding="utf-8") as f:
        synthetic_items = json.load(f)

    state = make_sunday_initial_state(run_id="test-approval-1")
    state["scored_items"] = synthetic_items
    state.update(read_trello(state))
    state.update(correlate_trello(state))
    state.update(classify_item(state))

    print(f"Pending approvals: {len(state['pending_approvals'])}")
    if not state["pending_approvals"]:
        print("No proposals produced — nothing to test. Check synthetic data.")
        raise SystemExit(0)

    result = compiled.invoke({"pending_approvals": state["pending_approvals"]}, config=config)

    interrupts = result.get("__interrupt__", [])
    print(f"\nPaused on {len(interrupts)} proposal(s) — check Telegram for real messages now.")
    for i in interrupts:
        print(f"  id={i.id} value={i.value}")

    print(f"\nRun this same script again to resume (thread_id='{THREAD_ID}').")