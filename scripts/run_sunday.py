import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# --dry-run: skips mark_seen() in score_node so manual testing doesn't
# permanently exhaust the real seen_items pool. Everything else (real
# fetches, real Anthropic scoring calls, real Telegram sends, real
# run_history/node_summary logging) still happens for real.
dry_run = "--dry-run" in sys.argv

from dotenv import load_dotenv
load_dotenv()

from core.logging_config import setup_logging
setup_logging()

from sunday.graph import build_sunday_graph
from core.state import make_sunday_initial_state
from core.checkpointer_config import DEFAULT_RECURSION_LIMIT
from core.observability import record_run_started, record_run_history

run_id = str(uuid.uuid4())
thread_id = run_id  # same value — makes checkpoint identifiable by run_id
started_at = datetime.now(timezone.utc)
t0 = time.perf_counter()

# Written before any real work starts (graph build, checkpointer
# connection, the actual invoke) -- a hard external kill (GitHub Actions
# cancelling on timeout-minutes, the exact real failure mode this is for)
# may never let the finally block below run at all. See
# core/observability.py's module docstring.
record_run_started(path="sunday", run_id=run_id, started_at=started_at.isoformat())

config = {
    "configurable": {"thread_id": thread_id},
    "recursion_limit": DEFAULT_RECURSION_LIMIT,
}

graph = build_sunday_graph()

print(f"Starting Sunday run {run_id[:8]} (thread_id={thread_id})" + (" [DRY RUN -- mark_seen() disabled]" if dry_run else ""))

# run_history must still get a real record on a crash, not just a clean
# finish -- see run_daily.py for the same reasoning. Re-raises so the
# GitHub Actions job still fails loudly on a real error.
status = "failed"
final_state = None
error_summary = None
try:
    final_state = graph.invoke(make_sunday_initial_state(run_id=run_id, dry_run=dry_run), config=config)
    status = "success"
except Exception as e:
    error_summary = f"{type(e).__name__}: {e}"
    raise
finally:
    duration = time.perf_counter() - t0
    scored = final_state.get("scored_items", []) if final_state else []
    kept = [i for i in scored if i.get("keep")]
    total_cost = sum(c.get("cost_usd", 0.0) for c in final_state.get("costs", [])) if final_state else 0.0
    # A paused run (proposals awaiting Telegram approval) is a normal,
    # expected outcome, not a failure -- distinguished from a genuine crash.
    if final_state is not None:
        snapshot = graph.get_state(config)
        if snapshot.next:
            status = "paused"
    record_run_history(
        path="sunday",
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

plan_items = [i for i in final_state.get("classified_items", []) if i.get("classification") == "plan_item"]
proposals = final_state.get("pending_approvals", [])

print(f"Scored: {len(kept)} kept / {len(scored)} total")
print(f"Plan: {len(plan_items)} plan_items · {len(proposals)} proposals pending approval")
print(f"Total cost: ${total_cost:.4f}")
if final_state.get("errors"):
    print(f"Errors: {final_state['errors']}")

if status == "paused":
    print(f"\nGraph paused — awaiting Telegram replies for {len(proposals)} proposal(s).")
    print(f"Thread ID for resume: {thread_id}")
    print("Reply 'approve' or 'reject' to each proposal message in Telegram.")
else:
    print(f"\nRun {run_id[:8]} complete.")
