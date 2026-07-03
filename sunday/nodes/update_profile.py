import csv
import time
import logging
import uuid
from pathlib import Path
from datetime import datetime, timezone

import anthropic

from state import SundayGraphState, NodeCost
from sunday.memory_store_config import get_store

logger = logging.getLogger(__name__)

client = anthropic.Anthropic()

TASTE_PROFILE_PATH = Path("data/taste_profile.yaml")

_YAML_PLACEHOLDER = """\
# Weekly Intel — taste profile
# Updated incrementally each Sunday based on rejected project proposals.
# (no rejection history yet — this is the initial version)
version: 1
proposal_filters: []
notes: ""
"""

_UPDATE_PROMPT = """\
You are maintaining a taste-profile YAML file for a weekly intelligence agent. \
The profile records which kinds of project proposals the user has rejected, \
so future classification runs can route similar ideas differently.

Current profile:
---
{current_profile}
---

This week's rejected proposals ({count} total):
{rejected_items}

Task: produce a SMALL incremental update to the profile above.
- Add or refine entries under proposal_filters to reflect the pattern of these rejections.
- Preserve all existing entries — do NOT regenerate from scratch.
- Do NOT overfit to a single ambiguous rejection; only add a pattern if it is clear.
- Return only valid YAML (no markdown fences, no commentary outside YAML).
"""


def _format_rejected_items(rejected: list[dict]) -> str:
    lines = []
    for i, item in enumerate(rejected, 1):
        lines.append(f"{i}. url: {item.get('url', 'unknown')}")
        lines.append(f"   summary: {item.get('text', '')[:200]}")
        lines.append(f"   proposal_type: {item.get('proposal_type')}")
        lines.append(f"   classification_reasoning: {item.get('classification_reasoning', '')[:200]}")
    return "\n".join(lines)


def _write_rejection_events(
    store, approval_results: list[dict], classified_items: list[dict], run_id: str
) -> NodeCost:
    t0 = time.perf_counter()
    rejected = [r for r in approval_results if r["decision"] != "approve"]
    for r in rejected:
        item = next((i for i in classified_items if i["url"] == r["item_id"]), None)
        if item is None:
            logger.warning(f"update_profile: no matching item for rejected {r['item_id']}")
            continue
        key = str(uuid.uuid4())
        namespace = ("weekly_intel", "rejection_events")
        value = {
            "type": "rejection_event",
            "item_id": r["item_id"],
            "content_summary": item.get("text", "")[:300],
            "proposal_type": item.get("proposal_type"),
            "classification_reasoning": item.get("classification_reasoning", ""),
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        store.put(namespace, key, value)
        logger.info(f"update_profile: wrote rejection_event {key} for {r['item_id']}")
    return NodeCost(
        node_name="write_rejection_events",
        input_tokens=0, output_tokens=0, cost_usd=0.0,
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )


def _regenerate_yaml_preferences(rejected_items: list[dict]) -> NodeCost:
    t0 = time.perf_counter()
    if not rejected_items:
        logger.info("update_profile: no rejections this run, skipping preference update")
        return NodeCost(
            node_name="update_profile_yaml",
            input_tokens=0, output_tokens=0, cost_usd=0.0, latency_ms=0.0,
        )

    current_profile = (
        TASTE_PROFILE_PATH.read_text(encoding="utf-8")
        if TASTE_PROFILE_PATH.exists()
        else _YAML_PLACEHOLDER
    )

    prompt = _UPDATE_PROMPT.format(
        current_profile=current_profile,
        count=len(rejected_items),
        rejected_items=_format_rejected_items(rejected_items),
    )

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    updated_yaml = response.content[0].text.strip()
    TASTE_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TASTE_PROFILE_PATH.write_text(updated_yaml, encoding="utf-8")
    logger.info(f"update_profile: wrote updated taste profile ({len(updated_yaml)} chars)")

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    return NodeCost(
        node_name="update_profile_yaml",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round((input_tokens * 0.00025 + output_tokens * 0.00125) / 1000, 6),
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )


def update_profile(state: SundayGraphState) -> dict:
    store = get_store()

    cost_store = _write_rejection_events(
        store, state["approval_results"], state["classified_items"], state["run_id"]
    )

    rejected_items = [
        i for i in state["classified_items"]
        if any(
            r["item_id"] == i["url"] and r["decision"] != "approve"
            for r in state["approval_results"]
        )
    ]
    cost_yaml = _regenerate_yaml_preferences(rejected_items)

    prior_cost = sum(c["cost_usd"] for c in state["costs"])
    total_cost = prior_cost + cost_store["cost_usd"] + cost_yaml["cost_usd"]
    logger.info(f"update_profile: run {state['run_id']} total cost ${total_cost:.4f}")

    plan_items = sum(1 for i in state["classified_items"] if i.get("classification") == "plan_item")
    proposals = len(state["pending_approvals"])
    rejections = len(rejected_items)

    cost_log = Path("data/cost_log.csv")
    write_header = not cost_log.exists()
    with cost_log.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["run_id", "timestamp", "total_cost_usd", "plan_items", "proposals", "rejections"])
        writer.writerow([
            state["run_id"],
            datetime.now(timezone.utc).isoformat(),
            round(total_cost, 6),
            plan_items,
            proposals,
            rejections,
        ])
    logger.info(
        f"update_profile: appended cost_log — "
        f"{plan_items} plan_items, {proposals} proposals, {rejections} rejections, "
        f"${total_cost:.6f}"
    )

    return {"costs": [cost_store, cost_yaml]}
