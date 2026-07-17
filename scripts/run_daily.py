import time
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

from logging_config import setup_logging
setup_logging()

from daily.graph import build_daily_graph
from state import make_daily_initial_state
from observability import record_run_started, record_run_history

run_id = str(uuid.uuid4())
started_at = datetime.now(timezone.utc)
t0 = time.perf_counter()

# Written before any real work starts -- a hard external kill (GitHub
# Actions cancelling on timeout-minutes) may never let the finally block
# below run at all. This early write is what survives that case: a real
# status="in_progress" record, overwritten by the real final one below
# if the process gets that far. See observability.py's module docstring.
record_run_started(path="daily", run_id=run_id, started_at=started_at.isoformat())

graph = build_daily_graph().compile()

# run_history must still get a real record on a crash, not just a clean
# finish -- cost_log.csv's exact blind spot (only ever written at the very
# end of a successful graph traversal, so a run that dies early leaves no
# trace of itself at all). Wrapping the real invoke() in try/except and
# re-raising keeps the GitHub Actions job failing loudly on a real error --
# this only adds a durable record of what happened, never swallows it.
status = "failed"
final_state = None
error_summary = None
try:
    final_state = graph.invoke(
        make_daily_initial_state(run_id=run_id),
        config={"recursion_limit": 50},
    )
    status = "success"
except Exception as e:
    error_summary = f"{type(e).__name__}: {e}"
    raise
finally:
    duration = time.perf_counter() - t0
    scored = final_state.get("scored_items", []) if final_state else []
    kept = [i for i in scored if i["keep"]]
    total_cost = sum(c.get("cost_usd", 0.0) for c in final_state["costs"]) if final_state else 0.0
    record_run_history(
        path="daily",
        run_id=run_id,
        started_at=started_at.isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        total_cost_usd=total_cost,
        items_in=len(scored),
        items_out=len(kept),
        duration_seconds=round(duration, 2),
        error_summary=error_summary,
    )

print(f"Run {run_id[:8]} complete: {len(kept)} kept / {len(scored)} scored")
print(f"Total cost: ${total_cost:.4f}")
if final_state["errors"]:
    print(f"Errors: {final_state['errors']}")
