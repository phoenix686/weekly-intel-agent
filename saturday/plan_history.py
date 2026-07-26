"""
Store namespace ("weekly_intel", "plan_history"): records which real
Trello cards got surfaced as "Existing Project Work" plan items in a
given Saturday run, tied to run_id. Saturday plan LLM prioritization
checkpoint, sub-phase 3; schema revised in sub-phase 4.

One entry per run, keyed by run_id (same keying pattern as
core/observability.py's run_history/node_summary) -- entries accumulate
across weeks, never overwritten, since cross-week movement detection
(sub-phase 4) needs to compare against the MOST RECENT PRIOR entry, not
just whatever the latest one happens to be.

Schema revision (sub-phase 4): each entry originally stored bare
card_ids (list[str]). Real cross-week movement detection needs to know
which list a card was in when it was LAST surfaced, to tell whether it
has since changed lists -- a bare card_id can't support that comparison.
Each card is now recorded as {"card_id", "list_name"} instead of a plain
string. No real production data existed under the old shape yet (the
only entry ever written was a smoke-test entry, deleted after sub-phase
3's verification), so this is a clean schema change, not a migration.

Deliberately NOT wrapped in try/except on the write side: unlike
node_summary/run_history (pure observability, safe to silently lose),
plan_history is real domain data movement detection depends on for
correctness -- same distinction discovery/seen_items.py's mark_seen()
already makes for cross-run dedup, which also lets failures propagate
rather than swallowing them.

No langgraph imports.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from saturday.memory_store_config import get_store

logger = logging.getLogger(__name__)

_NAMESPACE = ("weekly_intel", "plan_history")


def record_plan_history(run_id: str, cards: list[dict]) -> None:
    """Record which Trello cards were surfaced as Existing Project Work
    plan items in this Saturday run. Each card is {"card_id", "list_name"}
    (the list it was actually rendered under). One entry per run_id;
    duplicate card_ids (multiple plan items can match the same card) are
    collapsed, keeping the first occurrence's list_name."""
    store = get_store()
    generated_at = datetime.now(timezone.utc).isoformat()

    deduped: dict[str, str] = {}
    for card in cards:
        deduped.setdefault(card["card_id"], card["list_name"])
    unique_cards = [{"card_id": cid, "list_name": name} for cid, name in sorted(deduped.items())]

    logger.debug(f"plan_history: BEFORE store.put() (run={run_id})")
    store.put(
        _NAMESPACE,
        run_id,
        {"run_id": run_id, "cards": unique_cards, "generated_at": generated_at},
    )
    logger.info(f"plan_history: recorded {len(unique_cards)} card(s) for run {run_id}")


def get_most_recent_prior_entry(current_run_id: str | None = None) -> dict | None:
    """Return the plan_history entry with the latest generated_at, excluding
    current_run_id (defensive -- normally the current run hasn't written its
    own entry yet by the time this is called, since assemble_plan writes
    plan_history and always runs after read_trello). Returns None if no
    entry exists yet (e.g. the very first Saturday run) -- permissive, same
    "nothing to compare against yet" default as taste_vectors.py's
    taste_prefilter when no topic vectors exist."""
    store = get_store()
    entries = [item.value for item in store.search(_NAMESPACE, limit=1000)]
    candidates = [e for e in entries if e["run_id"] != current_run_id]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e["generated_at"])
