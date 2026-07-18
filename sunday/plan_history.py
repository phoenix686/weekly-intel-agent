"""
Store namespace ("weekly_intel", "plan_history"): records which real
Trello card IDs got surfaced as "Existing Project Work" plan items in a
given Sunday run, tied to run_id. Sunday plan LLM prioritization
checkpoint, sub-phase 3.

One entry per run, keyed by run_id (same keying pattern as
observability.py's run_history/node_summary) -- entries accumulate
across weeks, never overwritten, since sub-phase 4's cross-week movement
detection needs to compare against the MOST RECENT PRIOR entry, not just
whatever the latest one happens to be. Finding "most recent prior" is
sub-phase 4's job, not built here -- this module only writes.

Deliberately NOT wrapped in try/except: unlike node_summary/run_history
(pure observability, safe to silently lose), plan_history is real domain
data sub-phase 4's movement detection will depend on for correctness --
same distinction discovery/seen_items.py's mark_seen() already makes for
cross-run dedup, which also lets failures propagate rather than
swallowing them.

No langgraph imports.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sunday.memory_store_config import get_store

logger = logging.getLogger(__name__)

_NAMESPACE = ("weekly_intel", "plan_history")


def record_plan_history(run_id: str, card_ids: list[str]) -> None:
    """Record which Trello card IDs were surfaced as Existing Project Work
    plan items in this Sunday run. One entry per run_id; duplicate
    card_ids (multiple plan items can match the same card) are collapsed."""
    store = get_store()
    generated_at = datetime.now(timezone.utc).isoformat()
    unique_card_ids = sorted(set(card_ids))
    logger.debug(f"plan_history: BEFORE store.put() (run={run_id})")
    store.put(
        _NAMESPACE,
        run_id,
        {"run_id": run_id, "card_ids": unique_card_ids, "generated_at": generated_at},
    )
    logger.info(f"plan_history: recorded {len(unique_card_ids)} card_id(s) for run {run_id}")
