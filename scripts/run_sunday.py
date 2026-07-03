import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from logging_config import setup_logging
setup_logging()

from sunday.graph import build_sunday_graph
from state import make_sunday_initial_state
from checkpointer_config import DEFAULT_RECURSION_LIMIT

run_id = str(uuid.uuid4())
thread_id = run_id  # same value — makes checkpoint identifiable by run_id

config = {
    "configurable": {"thread_id": thread_id},
    "recursion_limit": DEFAULT_RECURSION_LIMIT,
}

graph = build_sunday_graph()

print(f"Starting Sunday run {run_id[:8]} (thread_id={thread_id})")
final_state = graph.invoke(make_sunday_initial_state(run_id=run_id), config=config)

kept = [i for i in final_state.get("scored_items", []) if i.get("keep")]
plan_items = [i for i in final_state.get("classified_items", []) if i.get("classification") == "plan_item"]
proposals = final_state.get("pending_approvals", [])
total_cost = sum(c.get("cost_usd", 0.0) for c in final_state.get("costs", []))

print(f"Scored: {len(kept)} kept / {len(final_state.get('scored_items', []))} total")
print(f"Plan: {len(plan_items)} plan_items · {len(proposals)} proposals pending approval")
print(f"Total cost: ${total_cost:.4f}")
if final_state.get("errors"):
    print(f"Errors: {final_state['errors']}")

snapshot = graph.get_state(config)
if snapshot.next:
    print(f"\nGraph paused — awaiting Telegram replies for {len(proposals)} proposal(s).")
    print(f"Thread ID for resume: {thread_id}")
    print("Reply 'approve' or 'reject' to each proposal message in Telegram.")
else:
    print(f"\nRun {run_id[:8]} complete.")
