import logging
import json
import os
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

from langgraph.types import Command

from sunday.nodes.await_approval import get_proposal_graph
from sunday.nodes.discover_sources import get_source_proposal_graph
from sunday.approval_actions import handle_approval, handle_rejection
from sunday.source_discovery_actions import handle_source_approval, handle_source_rejection
from sunday.memory_store_config import get_store
import telegram.feedback_router as feedback_router

logger = logging.getLogger(__name__)

_OFFSET_NAMESPACE = ("weekly_intel", "polling_state")
_OFFSET_KEY = "update_offset"

_APPROVE_KEYWORDS = {"approve", "approved", "yes", "y", "ok", "okay", "go", "do it"}
_REJECT_KEYWORDS = {"reject", "rejected", "no", "n", "nope", "skip", "pass", "don't", "dont"}


def _get_updates(offset: int | None = None) -> list[dict]:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    params: dict = {}
    if offset is not None:
        params["offset"] = offset
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram getUpdates error: {result}")
    return result["result"]


def _normalize_decision(text: str) -> str | None:
    t = text.strip().lower()
    if t in _APPROVE_KEYWORDS:
        return "approve"
    if t in _REJECT_KEYWORDS:
        return "reject"
    return None


def poll_once() -> None:
    store = get_store()

    offset_item = store.get(_OFFSET_NAMESPACE, _OFFSET_KEY)
    offset = offset_item.value["value"] if offset_item else None

    updates = _get_updates(offset)
    if not updates:
        return

    for update in updates:
        _handle_update(update, store)

    new_offset = updates[-1]["update_id"] + 1
    store.put(_OFFSET_NAMESPACE, _OFFSET_KEY, {"value": new_offset})


def _handle_update(update: dict, store) -> None:
    message = update.get("message") or update.get("channel_post")
    if not message:
        return

    reply_to = message.get("reply_to_message")
    text = (message.get("text") or "").strip()

    if reply_to:
        reply_msg_id = str(reply_to["message_id"])
        resume_item = store.get(("weekly_intel", "pending_resume_map"), reply_msg_id)
        source_resume_item = store.get(("weekly_intel", "pending_source_resume_map"), reply_msg_id)

        if resume_item:
            _handle_approval_reply(reply_msg_id, text, resume_item.value, store)
        elif source_resume_item:
            _handle_source_approval_reply(reply_msg_id, text, source_resume_item.value, store)
        else:
            feedback_router.handle_feedback(message)
    else:
        _queue_adhoc(text, store)


def _handle_approval_reply(reply_msg_id: str, text: str, record: dict, store) -> None:
    thread_id = record["thread_id"]
    run_id = record["run_id"]

    decision = _normalize_decision(text)
    if decision is None:
        from telegram.bot_client import send_message
        send_message("Didn't catch that — reply \"approve\" or \"reject\" to this proposal.")
        logger.warning(f"polling: unrecognized reply '{text}' for thread {thread_id}")
        return

    child = get_proposal_graph()
    result = child.invoke(
        Command(resume=decision),
        config={"configurable": {"thread_id": thread_id}},
    )

    if decision == "approve":
        handle_approval(result, thread_id)
    else:
        handle_rejection(result, run_id)

    store.delete(("weekly_intel", "pending_resume_map"), reply_msg_id)
    logger.info(f"polling: resolved {record['proposal_id']} → {decision}")


def _handle_source_approval_reply(reply_msg_id: str, text: str, record: dict, store) -> None:
    thread_id = record["thread_id"]

    decision = _normalize_decision(text)
    if decision is None:
        from telegram.bot_client import send_message
        send_message("Didn't catch that — reply \"approve\" or \"reject\" to this source proposal.")
        logger.warning(f"polling: unrecognized reply '{text}' for source thread {thread_id}")
        return

    child = get_source_proposal_graph()
    result = child.invoke(
        Command(resume=decision),
        config={"configurable": {"thread_id": thread_id}},
    )

    candidate = {"name": result["name"], "feed_url": result["feed_url"], "bucket": result["bucket"]}
    if decision == "approve":
        handle_source_approval(candidate, result["bucket"])
    else:
        handle_source_rejection(candidate)

    store.delete(("weekly_intel", "pending_source_resume_map"), reply_msg_id)
    logger.info(f"polling: resolved source proposal {record['proposal_id']} → {decision}")


def _queue_adhoc(text: str, store) -> None:
    if not text:
        return
    key = str(uuid.uuid4())
    store.put(
        ("weekly_intel", "adhoc_queue"),
        key,
        {
            "text": text,
            "queued_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    logger.info(f"polling: queued ad-hoc message (key={key})")
