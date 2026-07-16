"""
Handles Telegram reply outcomes for proposals and digest/plan feedback.

handle_approval / handle_rejection: proposal-approval outcomes (Trello
card creation/update), unaffected by batch2-dedup-taste-spec.md Section
7 -- rejection is still routed through handle_feedback as negative
signal, but that function's body changed (see below).

handle_feedback: per Section 7 item 1 (item-feedback-logging), this now
ONLY logs a discrete feedback_events record and triggers the same-day
capped nudge (sunday/same_day_nudge.py) -- it no longer calls Haiku to
rewrite taste_profile.yaml, and no longer touches the YAML file at all.
That REPLACES the prior Part-7-era behavior (a full, uncapped, immediate
profile rewrite on every single reply, confirmed still running as of
this checkpoint's Section 0 investigation) -- restoring the intended
weekly-batch cadence. The consolidated Sunday rewrite now lives in
sunday/nodes/update_profile.py, which reads every feedback_events record
accumulated here since the last Sunday run.
"""

import logging
import uuid
from datetime import datetime, timezone

from sunday.trello_client import create_trello_card, update_trello_card, get_dump_list_id
from telegram.bot_client import send_message
from sunday.memory_store_config import get_store
from sunday.same_day_nudge import apply_nudge

logger = logging.getLogger(__name__)

_FEEDBACK_NAMESPACE = ("weekly_intel", "feedback_events")


def handle_approval(item: dict, thread_id: str) -> None:
    """Creates or updates the relevant Trello card, sends Telegram confirmation."""
    if item.get("proposal_type") == "extend" and item.get("matched_card_id"):
        card = update_trello_card(item["matched_card_id"], desc=item["reasoning"])
        send_message(f"✅ Updated card: {card['name']}\n{card['url']}")
    else:
        title = item.get("title") or item["text"][:80]
        card = create_trello_card(title, get_dump_list_id(), item["reasoning"])
        send_message(f"✅ Created new card: {card['name']}\n{card['url']}")
    logger.info(f"handle_approval: processed approval for {item.get('url')}")


def handle_feedback(item: dict, feedback_text: str, sentiment: str, run_id: str) -> None:
    """Logs a discrete feedback_events record and triggers the same-day
    capped nudge -- and stops there. No Haiku rewrite call, no
    taste_profile.yaml write, same-day. Works for BOTH positive and
    negative signal -- passive digest/plan feedback is not gated behind
    approval, it flows straight into the log."""
    store = get_store()

    key = str(uuid.uuid4())
    tags = item.get("tags", [])
    value = {
        "item_id": item.get("url"),
        "feedback_text": feedback_text,
        "replied_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        # Beyond the spec's literal 4-field list: kept so the Sunday
        # consolidated rewrite (update_profile.py) can join each record
        # against the item's original content without a separate lookup
        # store (none exists -- seen_items only records a bare url->seen
        # flag), and so same_day_nudge can look up tags without
        # re-deriving them. Flagged as an interpretation, not silently
        # assumed.
        "tags": tags,
        "title": item.get("title", ""),
        "content_summary": item.get("text", "")[:300],
        "sentiment": sentiment,
    }
    store.put(_FEEDBACK_NAMESPACE, key, value)
    logger.info(f"handle_feedback: logged feedback_events entry for {item.get('url')} (sentiment={sentiment})")

    nudge_costs = apply_nudge(item.get("url"), feedback_text, tags, run_id)
    nudge_cost_usd = sum(c["cost_usd"] for c in nudge_costs)
    nudge_errors = [c["error"] for c in nudge_costs if c.get("error")]
    if nudge_errors:
        logger.info(f"handle_feedback: same_day_nudge for {item.get('url')}: {nudge_errors[0]}")
    else:
        logger.info(f"handle_feedback: same_day_nudge for {item.get('url')} applied (${nudge_cost_usd:.6f})")


def handle_rejection(item: dict, run_id: str) -> None:
    """Proposal rejection -- always negative signal. Thin wrapper over
    handle_feedback, kept so existing callers (telegram/polling.py) don't
    need to change."""
    handle_feedback(item, feedback_text="rejected proposal", sentiment="negative", run_id=run_id)
