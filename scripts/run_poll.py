import time
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from core.logging_config import setup_logging
setup_logging()

from telegram.polling import poll_once
from core.observability import record_run_started, record_run_history

# poll_once() has no per-run cost of its own (pure Telegram/Postgres
# mechanics -- any real Anthropic cost happens inside handle_approval/
# handle_rejection/apply_nudge, already attributed to their own call
# sites, not re-summed here) and no natural run_id of its own the way a
# graph invocation does -- minted fresh here, same pattern as
# run_daily.py/run_sunday.py, just to give this run_history entry
# something to key on.
run_id = str(uuid.uuid4())
started_at = datetime.now(timezone.utc)
t0 = time.perf_counter()

# Written before poll_once() starts -- see core/observability.py's module
# docstring for why a finally block alone isn't enough under a hard
# external kill.
record_run_started(path="poll", run_id=run_id, started_at=started_at.isoformat())

status = "failed"
error_summary = None
updates_in = 0
try:
    result = poll_once()
    updates_in = result["updates_in"]
    status = "success"
except Exception as e:
    error_summary = f"{type(e).__name__}: {e}"
    raise
finally:
    duration = time.perf_counter() - t0
    record_run_history(
        path="poll",
        run_id=run_id,
        started_at=started_at.isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        total_cost_usd=0.0,
        items_in=updates_in,
        items_out=updates_in,
        duration_seconds=round(duration, 2),
        error_summary=error_summary,
    )

print(f"poll_once complete: {updates_in} update(s) processed")
