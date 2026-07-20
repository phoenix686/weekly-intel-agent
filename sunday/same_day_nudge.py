"""
Same-day capped taste-profile nudge (batch2-dedup-taste-spec.md Section
7). Runs immediately after item-feedback-logging writes a new
("weekly_intel","feedback_events") record (called from
sunday/approval_actions.py's handle_feedback) -- a fast, cheap, bounded
patch that sits on top of, and is cleared by, the slow, thorough Sunday
consolidated rewrite (sunday/nodes/update_profile.py). Never a
replacement for it.

A Haiku call classifies the reply's feedback_text into a direction
(up/down/neutral) and magnitude (mild/moderate/strong), mapped to fixed
values (+0.05/+0.10/+0.20, negative for down). Tags come from the item's
existing score_node tags (passed in, not re-derived). Multiple reactions
on the same tag in one week stack, capped at +/-0.3 total regardless of
how many reactions occur.

A failed or malformed classification degrades gracefully: no adjustment
is applied. The feedback itself is never lost either way -- it's already
durably logged to feedback_events by the caller before this runs.

No langgraph imports.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import anthropic

from sunday.memory_store_config import get_store
from core.state import NodeCost

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic()

_NAMESPACE = ("weekly_intel", "same_day_adjustments")
_CAP = 0.3
_MAGNITUDE_VALUES = {"mild": 0.05, "moderate": 0.10, "strong": 0.20}
_VALID_DIRECTIONS = {"up", "down", "neutral"}

_CLASSIFY_PROMPT = """Classify this feedback on an AI/tech content digest item into a \
direction and magnitude.

Feedback: "{feedback_text}"

direction: "up" (positive), "down" (negative), or "neutral" (no clear sentiment either way)
magnitude: "mild", "moderate", or "strong" -- how strongly worded the feedback is

Return ONLY valid JSON, no markdown, no commentary: {{"direction": "...", "magnitude": "..."}}"""


def _haiku_cost(input_tokens: int, output_tokens: int) -> float:
    return round((input_tokens * 0.00025 + output_tokens * 0.00125) / 1000, 6)


def _classify(feedback_text: str) -> tuple[str | None, str | None, int, int]:
    """Returns (direction, magnitude, input_tokens, output_tokens).
    direction/magnitude are None on any failure -- caller applies no
    adjustment in that case."""
    response = _client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=100,
        messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(feedback_text=feedback_text)}],
    )
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    parsed = json.loads(raw)

    direction = parsed.get("direction")
    magnitude = parsed.get("magnitude")
    if direction not in _VALID_DIRECTIONS or magnitude not in _MAGNITUDE_VALUES:
        return None, None, input_tokens, output_tokens
    return direction, magnitude, input_tokens, output_tokens


def _week_key(now: datetime) -> str:
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def apply_nudge(item_id: str, feedback_text: str, tags: list[str], run_id: str) -> list[NodeCost]:
    """Classifies feedback_text via Haiku, applies the mapped adjustment
    to every tag on this item, stacked and capped at +/-0.3 per tag per
    week. Returns one NodeCost (classification failures/no-tags cases)
    or one NodeCost per tag updated (normal case) -- caller (approval_actions.py)
    only logs these, doesn't thread them into any graph state."""
    t0 = time.perf_counter()

    if not tags:
        return [NodeCost(
            node_name="same_day_nudge", input_tokens=0, output_tokens=0,
            cost_usd=0.0, latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            error=f"item {item_id!r} has no tags, nudge skipped",
        )]

    try:
        direction, magnitude, input_tokens, output_tokens = _classify(feedback_text)
    except Exception as e:
        logger.warning(f"same_day_nudge: classification failed for {item_id!r}: {e}")
        return [NodeCost(
            node_name="same_day_nudge", input_tokens=0, output_tokens=0,
            cost_usd=0.0, latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            error=f"classification failed, no adjustment applied: {e}",
        )]

    cost_usd = _haiku_cost(input_tokens, output_tokens)
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)

    if direction is None:
        logger.warning(f"same_day_nudge: invalid classification shape for {item_id!r}")
        return [NodeCost(
            node_name="same_day_nudge", input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost_usd, latency_ms=latency_ms,
            error="invalid classification shape (direction/magnitude not recognized), no adjustment applied",
        )]

    delta = 0.0 if direction == "neutral" else (
        _MAGNITUDE_VALUES[magnitude] if direction == "up" else -_MAGNITUDE_VALUES[magnitude]
    )

    store = get_store()
    week_key = _week_key(datetime.now(timezone.utc))
    costs: list[NodeCost] = []

    for i, tag in enumerate(tags):
        key = f"{week_key}:{tag}"
        existing = store.get(_NAMESPACE, key)
        current_total = existing.value["cumulative_adjustment"] if existing else 0.0
        item_ids = list(existing.value["item_ids_contributing"]) if existing else []
        new_total = round(max(-_CAP, min(_CAP, current_total + delta)), 6)
        item_ids.append(item_id)
        store.put(_NAMESPACE, key, {
            "tag": tag,
            "cumulative_adjustment": new_total,
            "item_ids_contributing": item_ids,
            "week_of": week_key,
        })
        # Only the first tag's cost record carries the real token/cost
        # figures -- the Haiku call happened once, not once per tag.
        costs.append(NodeCost(
            node_name="same_day_nudge",
            input_tokens=input_tokens if i == 0 else 0,
            output_tokens=output_tokens if i == 0 else 0,
            cost_usd=cost_usd if i == 0 else 0.0,
            latency_ms=latency_ms,
        ))

    logger.info(f"same_day_nudge: applied {direction}/{magnitude} ({delta:+.2f}) to {len(tags)} tag(s) for {item_id!r}")
    return costs
