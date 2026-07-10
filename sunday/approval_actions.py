import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from sunday.trello_client import create_trello_card, update_trello_card, get_dump_list_id
from telegram.bot_client import send_message
from sunday.memory_store_config import get_store

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic()

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


def _format_rejection_for_yaml(item: dict) -> str:
    return "\n".join([
        f"1. url: {item.get('url', 'unknown')}",
        f"   summary: {item.get('text', '')[:200]}",
        f"   proposal_type: {item.get('proposal_type')}",
        f"   classification_reasoning: {item.get('classification_reasoning', '')[:200]}",
    ])


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


def handle_rejection(item: dict, run_id: str) -> None:
    """Writes rejection_event to the Postgres store and performs incremental YAML update."""
    store = get_store()

    key = str(uuid.uuid4())
    namespace = ("weekly_intel", "rejection_events")
    value = {
        "type": "rejection_event",
        "item_id": item["url"],
        "content_summary": item.get("text", "")[:300],
        "proposal_type": item.get("proposal_type"),
        "classification_reasoning": item.get("classification_reasoning", ""),
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    store.put(namespace, key, value)
    logger.info(f"handle_rejection: wrote rejection_event {key} for {item['url']}")

    _update_yaml_for_rejection(item)


def _update_yaml_for_rejection(item: dict) -> None:
    current_profile = (
        TASTE_PROFILE_PATH.read_text(encoding="utf-8")
        if TASTE_PROFILE_PATH.exists()
        else _YAML_PLACEHOLDER
    )

    prompt = _UPDATE_PROMPT.format(
        current_profile=current_profile,
        count=1,
        rejected_items=_format_rejection_for_yaml(item),
    )

    response = _client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    updated_yaml = response.content[0].text.strip()
    TASTE_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TASTE_PROFILE_PATH.write_text(updated_yaml, encoding="utf-8")

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    cost = round((input_tokens * 0.00025 + output_tokens * 0.00125) / 1000, 6)
    logger.info(
        f"_update_yaml_for_rejection: updated taste profile "
        f"(tokens: {input_tokens}/{output_tokens}, cost: ${cost:.6f})"
    )
