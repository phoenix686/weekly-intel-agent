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
# Updated incrementally based on rejected proposals and digest/plan feedback.
# (no feedback history yet — this is the initial version)
version: 1
proposal_filters: []
notes: ""
"""

_UPDATE_PROMPT = """\
You are maintaining a taste-profile YAML file for a weekly intelligence agent. \
The profile records patterns in what the user likes and dislikes -- both \
project proposals and daily/weekly digest items -- so future scoring and \
classification runs can route similar content appropriately.

Current profile:
---
{current_profile}
---

New signal ({sentiment}, {count} total):
{feedback_items}

Task: produce a SMALL incremental update to the profile above.
- Add or refine entries under proposal_filters to reflect this signal.
- Preserve all existing entries — do NOT regenerate from scratch.
- Do NOT overfit to a single ambiguous reaction; only add a pattern if it is clear.
- Return only valid YAML (no markdown fences, no commentary outside YAML).
"""


def _format_feedback_for_yaml(item: dict, feedback_text: str) -> str:
    return "\n".join([
        f"1. url: {item.get('url', 'unknown')}",
        f"   summary: {item.get('text', '')[:200]}",
        f"   feedback: {feedback_text[:200]}",
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


def handle_feedback(item: dict, feedback_text: str, sentiment: str, run_id: str) -> None:
    """Writes a feedback_event to the Postgres store and folds the signal
    into taste_profile.yaml via the same incremental Haiku-update mechanism
    used for proposal rejections. Works for BOTH positive and negative
    signal -- passive digest/plan feedback is not gated behind approval,
    it flows straight into the profile update."""
    store = get_store()

    key = str(uuid.uuid4())
    namespace = ("weekly_intel", "feedback_events")
    value = {
        "type": "feedback_event",
        "item_id": item.get("url"),
        "content_summary": item.get("text", "")[:300],
        "feedback_text": feedback_text,
        "sentiment": sentiment,
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    store.put(namespace, key, value)
    logger.info(f"handle_feedback: wrote feedback_event {key} for {item.get('url')} (sentiment={sentiment})")

    _update_yaml_for_feedback(item, feedback_text, sentiment)


def handle_rejection(item: dict, run_id: str) -> None:
    """Proposal rejection -- always negative signal. Thin wrapper over
    handle_feedback, kept so existing callers (telegram/polling.py) don't
    need to change."""
    handle_feedback(item, feedback_text="rejected proposal", sentiment="negative", run_id=run_id)


def _update_yaml_for_feedback(item: dict, feedback_text: str, sentiment: str) -> None:
    current_profile = (
        TASTE_PROFILE_PATH.read_text(encoding="utf-8")
        if TASTE_PROFILE_PATH.exists()
        else _YAML_PLACEHOLDER
    )

    prompt = _UPDATE_PROMPT.format(
        current_profile=current_profile,
        sentiment=sentiment,
        count=1,
        feedback_items=_format_feedback_for_yaml(item, feedback_text),
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
        f"_update_yaml_for_feedback: updated taste profile "
        f"(sentiment={sentiment}, tokens: {input_tokens}/{output_tokens}, cost: ${cost:.6f})"
    )
