import uuid
from dotenv import load_dotenv
load_dotenv()

from logging_config import setup_logging
setup_logging()

from daily.graph import build_daily_graph
from state import make_daily_initial_state

run_id = str(uuid.uuid4())
graph = build_daily_graph().compile()
final_state = graph.invoke(
    make_daily_initial_state(run_id=run_id),
    config={"recursion_limit": 50},
)

kept = [i for i in final_state["scored_items"] if i["keep"]]
total_cost = sum(c.get("cost_usd", 0.0) for c in final_state["costs"])
print(f"Run {run_id[:8]} complete: {len(kept)} kept / {len(final_state['scored_items'])} scored")
print(f"Total cost: ${total_cost:.4f}")
if final_state["errors"]:
    print(f"Errors: {final_state['errors']}")
