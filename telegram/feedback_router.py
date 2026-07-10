"""
Routes Telegram replies that are not approval responses.

Numbered digest/plan feedback (Part D) resolves item numbers back to real
items via the digest_item_map store entry keyed by the ORIGINAL message's
message_id, then folds the signal into taste_profile.yaml via
approval_actions.handle_feedback -- NOT gated behind approval, unlike
proposal approve/reject.

`_parse_numbered_feedback` turns free-form reply text like "1. loved it,
2. meh" into structured [{item_number, feedback_text, sentiment}, ...] via
a Haiku call, same structured-output pattern as classify_item.py (prompt
-> parse -> retry-on-malformed-JSON -> defensive validation). Sentiment
inference is folded into this same call rather than a second LLM
round-trip, since it's the same judgment the parsing call is already
making.
"""

import json
import logging

import anthropic

from sunday.approval_actions import handle_feedback as apply_feedback
from sunday.memory_store_config import get_store

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic()

_DIGEST_MAP_NAMESPACE = ("weekly_intel", "digest_item_map")

_PARSE_PROMPT = """You are parsing a user's free-form reply to a numbered list of digest/plan \
items into structured per-item feedback.

The numbered items shown to the user were:
{items_block}

The user's reply:
{reply_text}

For each item NUMBER the user gave feedback on, determine:
- item_number: the integer number they referenced
- feedback_text: their feedback in their own words (brief, verbatim or lightly cleaned up)
- sentiment: "positive" or "negative" -- infer from tone/content

Not every item needs to be covered -- only include items the user actually gave feedback on. \
Ignore anything in the reply that doesn't reference the shown list.

Return ONLY a JSON array, one object per referenced item, in this exact shape:
[{{"item_number": 1, "feedback_text": "...", "sentiment": "positive" or "negative"}}]"""

_VALID_SENTIMENTS = {"positive", "negative"}


def _format_items_for_parse(item_map: dict) -> str:
    def _sort_key(kv):
        try:
            return int(kv[0])
        except (TypeError, ValueError):
            return 0

    lines = []
    for number, item in sorted(item_map.items(), key=_sort_key):
        title = item.get("title") or item.get("url", "unknown")
        lines.append(f"{number}. {title}")
    return "\n".join(lines)


def _parse_json_response(raw: str) -> list:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw.strip())


def _parse_numbered_feedback(text: str, item_map: dict | None = None) -> list[dict]:
    """Turns free-form reply text into structured per-item feedback +
    sentiment via a Haiku call. item_map (the same shape stored in
    digest_item_map) is included as context so loosely-worded replies
    ("the langchain one was great") can still resolve to the right
    number, not just strictly-numbered replies."""
    items_block = _format_items_for_parse(item_map) if item_map else "(not available)"
    prompt = _PARSE_PROMPT.format(items_block=items_block, reply_text=text)

    response = _client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    try:
        parsed = _parse_json_response(response.content[0].text)
    except json.JSONDecodeError:
        logger.warning("feedback_router: numbered-feedback parse failed, retrying")
        retry = _client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response.content[0].text},
                {"role": "user", "content": "Return ONLY valid JSON. No markdown, no text before or after the array."},
            ],
        )
        try:
            parsed = _parse_json_response(retry.content[0].text)
        except json.JSONDecodeError:
            logger.error("feedback_router: numbered-feedback parse failed after retry")
            return []

    valid: list[dict] = []
    for entry in parsed:
        if entry.get("sentiment") not in _VALID_SENTIMENTS:
            logger.warning(f"feedback_router: dropping entry with invalid sentiment: {entry}")
            continue
        if not isinstance(entry.get("item_number"), int):
            logger.warning(f"feedback_router: dropping entry with invalid item_number: {entry}")
            continue
        valid.append(entry)
    return valid


def _handle_numbered_feedback(reply_to_message_id: int, text: str) -> bool:
    """Returns True if this reply was a numbered digest/plan reply (and
    was processed), False if there's no matching digest_item_map entry --
    caller should fall through to the generic unrouted-reply log."""
    entry = get_store().get(_DIGEST_MAP_NAMESPACE, str(reply_to_message_id))
    if entry is None:
        return False

    run_id = entry.value["run_id"]
    item_map = entry.value["items"]  # keys are strings once round-tripped through the store

    parsed = _parse_numbered_feedback(text, item_map)
    for feedback_entry in parsed:
        number = str(feedback_entry["item_number"])
        item = item_map.get(number)
        if item is None:
            logger.warning(f"feedback_router: no item found for number {feedback_entry['item_number']}")
            continue
        apply_feedback(
            item,
            feedback_text=feedback_entry["feedback_text"],
            sentiment=feedback_entry["sentiment"],
            run_id=run_id,
        )

    logger.info(f"feedback_router: processed {len(parsed)} numbered feedback item(s)")
    return True


def handle_feedback(message: dict) -> None:
    """Route a Telegram reply that isn't an approval response. If it's a
    reply to a known digest/plan message, resolve numbered feedback and
    fold it into taste_profile.yaml. Otherwise, just log it (unrouted)."""
    reply_to = message.get("reply_to_message")
    text = (message.get("text") or "").strip()

    if reply_to and text and _handle_numbered_feedback(reply_to["message_id"], text):
        return

    logger.info(
        f"feedback_router: unrouted reply "
        f"(message_id={message.get('message_id')}): "
        f"{(message.get('text') or '')[:100]}"
    )
