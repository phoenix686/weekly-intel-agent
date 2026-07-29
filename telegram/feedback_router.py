"""Two-phase natural-language feedback: parse, confirm, then learn."""
from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import anthropic
from core.preferences import apply_confirmed_events
from saturday.approval_actions import handle_feedback as log_feedback
from saturday.memory_store_config import get_store
from telegram.bot_client import send_message

logger = logging.getLogger(__name__)
_client = anthropic.Anthropic()
_DIGEST_MAP_NAMESPACE = ("weekly_intel", "digest_item_map")
_PENDING_NAMESPACE = ("weekly_intel", "pending_feedback")
_CONFIRM = {"confirm", "confirmed", "yes", "apply"}
_CANCEL = {"cancel", "no", "discard"}

_PROMPT = """Parse the user's reply into JSON feedback events. Scale relevance:
0=avoid, 1=informational/okay, 2=useful, 3=loved/must-read.
Capture duplicate_of item number, liked_aspects, disliked_aspects, new_interests,
topics, and digest_flags (including redundant_coverage). One event per referenced
item; digest-level criticism may use item_number null. Do not invent feedback.
Items:
{items}
Reply:
{reply}
Return only a JSON array with keys item_number, relevance, duplicate_of,
liked_aspects, disliked_aspects, new_interests, topics, digest_flags, feedback_text."""


def _format_items_for_parse(item_map: dict) -> str:
    return "\n".join(f"{k}. {v.get('title') or v.get('url')}" for k, v in item_map.items())


def _parse_json_response(raw: str) -> list:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(raw)


def _parse_numbered_feedback(text: str, item_map: dict | None = None) -> list[dict]:
    response = _client.messages.create(
        model="claude-haiku-4-5", max_tokens=1024,
        messages=[{"role": "user", "content": _PROMPT.format(
            items=_format_items_for_parse(item_map or {}), reply=text)}],
    )
    try:
        parsed = _parse_json_response(response.content[0].text)
    except (json.JSONDecodeError, IndexError):
        logger.exception("feedback parse failed; no preference state changed")
        return []
    return [event for event in parsed if event.get("item_number") is None
            or str(event.get("item_number")) in {str(k) for k in (item_map or {})}]


def _summary(events: list[dict]) -> str:
    parts = []
    for event in events:
        prefix = "digest" if event.get("item_number") is None else str(event["item_number"])
        value = f"{prefix}={event.get('relevance', 1)}"
        if event.get("duplicate_of"):
            value += f" duplicate of {event['duplicate_of']}"
        labels = event.get("topics", []) + event.get("new_interests", []) + event.get("digest_flags", [])
        if labels:
            value += " " + "/".join(labels[:3])
        parts.append(value)
    return "; ".join(parts)


def _confirm(reply_id: int, text: str, store) -> bool:
    pending_item = store.get(_PENDING_NAMESPACE, str(reply_id))
    if not pending_item:
        return False
    pending = pending_item.value
    if text.lower() in _CANCEL:
        store.delete(_PENDING_NAMESPACE, str(reply_id))
        send_message("Feedback discarded.")
        return True
    if text.lower() not in _CONFIRM:
        send_message('Reply "confirm" to apply this feedback, or "cancel" to discard it.')
        return True
    events = pending["events"]
    apply_confirmed_events(store, events)
    item_map = pending["item_map"]
    for event in events:
        if event.get("item_number") is None:
            continue
        item = item_map.get(str(event["item_number"])) or item_map.get(event["item_number"])
        if item:
            log_feedback(item, event.get("feedback_text", ""), "positive" if event["relevance"] >= 2 else "negative",
                         pending["run_id"])
    store.delete(_PENDING_NAMESPACE, str(reply_id))
    send_message(f"Applied {len(events)} confirmed feedback signal(s).")
    return True


def _handle_numbered_feedback(reply_id: int, text: str) -> bool:
    store = get_store()
    entry = store.get(_DIGEST_MAP_NAMESPACE, str(reply_id))
    if not entry:
        return False
    events = _parse_numbered_feedback(text, entry.value["items"])
    if not events:
        send_message("I couldn't parse that feedback, so nothing was changed.")
        return True
    for event in events:
        event.update({"event_id": str(uuid.uuid4()), "confirmation_state": "pending",
                      "parser_confidence": event.get("parser_confidence", .8), "raw_text": text})
    response = send_message(f"Please confirm: {_summary(events)}\n\nReply “confirm” or “cancel”.")
    confirmation_id = str(response["result"]["message_id"])
    store.put(_PENDING_NAMESPACE, confirmation_id, {
        "events": events, "item_map": entry.value["items"], "run_id": entry.value["run_id"],
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    })
    return True


def handle_feedback(message: dict) -> None:
    reply = message.get("reply_to_message")
    text = (message.get("text") or "").strip()
    if not reply or not text:
        return
    store = get_store()
    if _confirm(reply["message_id"], text, store):
        return
    if not _handle_numbered_feedback(reply["message_id"], text):
        logger.info("feedback_router: unrouted reply message_id=%s", message.get("message_id"))
