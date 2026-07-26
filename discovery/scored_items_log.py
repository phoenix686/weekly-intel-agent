"""
Durable audit log of score_node's full per-item output (2026-07-23 fix):
before this, the only thing that survived a run past ephemeral process
state was seen_items' bare {seen, seen_at} flag -- keep/reasoning/tags for
every scored item vanished the moment the process exited, making post-hoc
investigation of what got dropped and why (and, combined with digest
truncation, what got kept but never shown) permanently impossible. A real
investigation this session had to reconstruct a partial item list from
seen_items' bare URLs plus digest_item_map's already-truncated slice --
this closes that gap going forward.

One record per run_id under ("weekly_intel","scored_items_log"), holding
every ScoredItem exactly as score_node produced it (both keep=True and
keep=False -- the whole point is auditing drops, not just kept items).

A failed write here must never block the real scoring outcome it's
describing -- same reliability requirement as every other observability
write in this project (approval_log, node_summary, embedding_failures).

No langgraph imports.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.state import ScoredItem
from saturday.memory_store_config import get_store

logger = logging.getLogger(__name__)

_NAMESPACE = ("weekly_intel", "scored_items_log")


def log_scored_items(run_id: str, items: list[ScoredItem]) -> None:
    """Persists the full scored_items list for this run, keyed by run_id.
    Unconditional (unlike mark_seen, not gated behind dry_run) -- this is
    an audit record, not a state-mutating write that affects future runs'
    dedup correctness, so there's no reason a dry/manual run's items
    shouldn't be just as inspectable afterward."""
    try:
        get_store().put(
            _NAMESPACE,
            run_id,
            {
                "run_id": run_id,
                "items": list(items),
                "item_count": len(items),
                "logged_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as e:
        logger.warning(f"scored_items_log: write failed for run {run_id}: {e}")
