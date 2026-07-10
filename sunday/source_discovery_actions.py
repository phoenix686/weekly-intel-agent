"""
Plain functions (no LangGraph) for resolving source-discovery proposals
(Part C steps 5-6). Mirrors approval_actions.py's handle_approval/
handle_rejection split -- intended to be called from wherever the
Send-based per-candidate proposal subgraph resolves (that subgraph itself
reuses await_approval.py's dedicated-thread pattern and is Pooja's to
build, per the same LangGraph-authorship line as the rest of this file's
sibling).
"""

import logging
from datetime import datetime, timezone

from discovery.source_config import add_source
from sunday.memory_store_config import get_store
from telegram.bot_client import send_message

logger = logging.getLogger(__name__)

_REJECTED_NAMESPACE = ("weekly_intel", "rejected_source_candidates")


def handle_source_approval(candidate: dict, bucket: str) -> None:
    """Adds the approved candidate to data/sources.json and confirms via
    Telegram. candidate: {"name": str, "feed_url": str, ...}."""
    add_source(bucket, candidate["name"], candidate["feed_url"])
    send_message(f"Added new source: {candidate['name']} ({bucket})")
    logger.info(f"handle_source_approval: added {candidate['name']} to {bucket} bucket")


def handle_source_rejection(candidate: dict) -> None:
    """Records the rejected candidate (keyed by feed_url) so it's never
    re-proposed on a future weekly run."""
    store = get_store()
    store.put(
        _REJECTED_NAMESPACE,
        candidate["feed_url"],
        {
            "type": "rejected_source_candidate",
            "name": candidate["name"],
            "feed_url": candidate["feed_url"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    logger.info(f"handle_source_rejection: recorded rejection for {candidate['name']}")


def is_already_rejected(feed_url: str) -> bool:
    store = get_store()
    return store.get(_REJECTED_NAMESPACE, feed_url) is not None
