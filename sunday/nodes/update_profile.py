"""
Sunday consolidated taste-profile rewrite (batch2-dedup-taste-spec.md
Section 7, item 2 -- file-layout decision: this file, not
approval_actions.py. approval_actions.py's handlers are live per-reply
functions called from telegram/polling.py outside any graph invocation,
with no natural "Sunday path" signal reachable there; this node is
already invoked exactly once per Sunday graph run, which is exactly the
cadence this mechanism needs).

Reads every ("weekly_intel","feedback_events") record from the last
_LOOKBACK_DAYS, joins each against the content captured on it at
log-time (item-feedback-logging, sunday/approval_actions.py), and
produces ONE consolidated taste_profile.yaml rewrite via a single Haiku
call -- not one call per reply, considering the whole week's pattern
together (three positive reactions to one topic and one negative
outlier are not equally weighted signals). Immediately after,
recomputes topic vectors from the fresh text (discovery/taste_vectors.py)
and clears same_day_adjustments for the new week, since the full week's
feedback has now been properly absorbed into the batch rewrite.

_LOOKBACK_DAYS=7 approximates "since the last Sunday run": no separate
last-run marker exists in the store, so this reuses the same rolling-
window pattern already established for recent_item_embeddings. Flagged
as an interpretation, not the spec's literal text.

Also still does the pre-existing cost_log.csv accounting for the whole
Sunday run.
"""

import csv
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic

from state import SundayGraphState, NodeCost
from sunday.memory_store_config import get_store
from discovery.taste_vectors import recompute_topic_vectors

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic()

TASTE_PROFILE_PATH = Path("data/taste_profile.yaml")
_FEEDBACK_NAMESPACE = ("weekly_intel", "feedback_events")
_SAME_DAY_NAMESPACE = ("weekly_intel", "same_day_adjustments")
_LOOKBACK_DAYS = 7

_YAML_PLACEHOLDER = """\
# Weekly Intel — taste profile
# Updated incrementally based on rejected proposals and digest/plan feedback.
# (no feedback history yet — this is the initial version)
version: 1
proposal_filters: []
notes: ""
"""

_REWRITE_PROMPT = """\
You are maintaining a taste-profile YAML file for a weekly intelligence agent. \
The profile records patterns in what the user likes and dislikes -- both \
project proposals and daily/weekly digest items -- so future scoring and \
classification runs can route similar content appropriately.

Current profile:
---
{current_profile}
---

This week's feedback ({count} item(s) -- weigh the whole pattern together: \
e.g. several positive reactions to one topic and a single negative outlier \
should NOT be treated as equally weighted signals):
{feedback_block}

Task: produce ONE consolidated incremental update to the profile above, \
considering the whole week's feedback together, not one edit per item.
- Add or refine entries under proposal_filters to reflect clear patterns.
- Preserve all existing entries — do NOT regenerate from scratch.
- Do NOT overfit to a single ambiguous or outlier reaction.
- Return only valid YAML (no markdown fences, no commentary outside YAML)."""


def _format_feedback_block(records: list[dict]) -> str:
    lines = []
    for i, r in enumerate(records, 1):
        tags = ", ".join(r.get("tags") or []) or "none"
        lines.append(
            f"{i}. url: {r.get('item_id', 'unknown')}\n"
            f"   title: {(r.get('title') or '')[:100]}\n"
            f"   tags: {tags}\n"
            f"   summary: {(r.get('content_summary') or '')[:200]}\n"
            f"   feedback ({r.get('sentiment', 'unknown')}): {(r.get('feedback_text') or '')[:200]}"
        )
    return "\n".join(lines)


def _load_recent_feedback(cutoff: datetime) -> list[dict]:
    store = get_store()
    records = []
    for item_obj in store.search(_FEEDBACK_NAMESPACE, limit=500):
        value = item_obj.value
        replied_at = value.get("replied_at")
        if not replied_at:
            continue
        if datetime.fromisoformat(replied_at) >= cutoff:
            records.append(value)
    return records


def _clear_same_day_adjustments() -> int:
    store = get_store()
    cleared = 0
    for item_obj in store.search(_SAME_DAY_NAMESPACE, limit=1000):
        store.delete(_SAME_DAY_NAMESPACE, item_obj.key)
        cleared += 1
    return cleared


def _consolidated_rewrite(records: list[dict]) -> list[NodeCost]:
    """One Haiku call over the whole week's feedback, writes
    taste_profile.yaml, then recomputes topic vectors from the fresh
    text. Returns the accumulated cost records."""
    current_profile = (
        TASTE_PROFILE_PATH.read_text(encoding="utf-8")
        if TASTE_PROFILE_PATH.exists()
        else _YAML_PLACEHOLDER
    )

    prompt = _REWRITE_PROMPT.format(
        current_profile=current_profile,
        count=len(records),
        feedback_block=_format_feedback_block(records),
    )

    response = _client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1536,
        messages=[{"role": "user", "content": prompt}],
    )

    updated_yaml = response.content[0].text.strip()
    TASTE_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TASTE_PROFILE_PATH.write_text(updated_yaml, encoding="utf-8")

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    cost_usd = round((input_tokens * 0.00025 + output_tokens * 0.00125) / 1000, 6)
    logger.info(
        f"update_profile: consolidated rewrite over {len(records)} feedback item(s) "
        f"(tokens: {input_tokens}/{output_tokens}, cost: ${cost_usd:.6f})"
    )

    costs = [NodeCost(
        node_name="update_profile", input_tokens=input_tokens, output_tokens=output_tokens,
        cost_usd=cost_usd, latency_ms=0.0,
    )]

    vector_costs = recompute_topic_vectors(updated_yaml)
    costs.extend(vector_costs)
    vector_ok = sum(1 for c in vector_costs if not c.get("error"))
    logger.info(f"update_profile: recomputed {vector_ok}/{len(vector_costs)} topic vectors after rewrite")

    return costs


def update_profile(state: SundayGraphState) -> dict:
    t0 = time.perf_counter()
    costs: list[NodeCost] = []

    cutoff = datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)
    records = _load_recent_feedback(cutoff)

    if records:
        costs.extend(_consolidated_rewrite(records))
    else:
        logger.info("update_profile: no feedback_events since last Sunday, profile left unchanged")

    cleared = _clear_same_day_adjustments()
    logger.info(f"update_profile: cleared {cleared} same_day_adjustments entr(y/ies) for the new week")

    total_cost = sum(c["cost_usd"] for c in state["costs"]) + sum(c["cost_usd"] for c in costs)
    plan_items = sum(1 for i in state["classified_items"] if i.get("classification") == "plan_item")
    proposals = len(state["pending_approvals"])

    cost_log = Path("data/cost_log.csv")
    write_header = not cost_log.exists()
    with cost_log.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["run_id", "timestamp", "total_cost_usd", "plan_items", "proposals"])
        writer.writerow([
            state["run_id"],
            datetime.now(timezone.utc).isoformat(),
            round(total_cost, 6),
            plan_items,
            proposals,
        ])

    logger.info(
        f"update_profile: run {state['run_id']} — "
        f"{plan_items} plan_items, {proposals} proposals pending, "
        f"${total_cost:.6f}"
    )

    costs.append(NodeCost(
        node_name="update_profile",
        input_tokens=0, output_tokens=0, cost_usd=0.0,
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    ))
    return {"costs": costs}
