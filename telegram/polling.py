import logging
import json
import os
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

from langgraph.types import Command

from saturday.nodes.await_approval import get_proposal_graph
from saturday.approval_actions import handle_approval, handle_rejection
from saturday.memory_store_config import get_store
import telegram.feedback_router as feedback_router
from telegram.update_inbox import process_inbox

logger = logging.getLogger(__name__)

_OFFSET_NAMESPACE = ("weekly_intel", "polling_state")
_OFFSET_KEY = "update_offset"
_PROCESSED_NAMESPACE = ("weekly_intel", "telegram_updates")
_EFFECT_NAMESPACE = ("weekly_intel", "telegram_update_effects")
TELEGRAM_GET_UPDATES_TIMEOUT_SECONDS = 10

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
    with urllib.request.urlopen(url, timeout=TELEGRAM_GET_UPDATES_TIMEOUT_SECONDS) as resp:
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


def poll_once() -> dict:
    """Returns {"updates_in": N} -- how many real Telegram updates were
    fetched this call (0 if none). Every fetched update gets routed to
    exactly one of the three paths (resume/feedback/ad-hoc) in
    _handle_update, so there's no separate "processed" count to track
    beyond this -- callers wanting run_history's items_in/items_out use
    this same number for both."""
    inbox_mode = os.getenv("TELEGRAM_INBOX_MODE", "").lower() == "true"
    if inbox_mode and (not os.getenv("TELEGRAM_CHAT_ID") or not os.getenv("TELEGRAM_USER_ID")):
        raise RuntimeError("TELEGRAM_CHAT_ID and TELEGRAM_USER_ID are required")
    store = get_store()
    if inbox_mode:
        result = process_inbox(lambda update: _handle_update(update, store))
        return {"updates_in": result["processed"], **result}

    offset_item = store.get(_OFFSET_NAMESPACE, _OFFSET_KEY)
    offset = offset_item.value["value"] if offset_item else None

    updates = _get_updates(offset)
    processed = 0
    for update in updates:
        update_id = str(update["update_id"])
        if store.get(_PROCESSED_NAMESPACE, update_id):
            continue
        _handle_update(update, store)
        store.put(_PROCESSED_NAMESPACE, update_id, {
            "status": "processed", "processed_at": datetime.now(timezone.utc).isoformat()
        })
        processed += 1
    if updates:
        store.put(_OFFSET_NAMESPACE, _OFFSET_KEY, {"value": updates[-1]["update_id"] + 1})
    return {"updates_in": processed}


def _handle_update(update: dict, store) -> None:
    effect_key = str(update["update_id"])
    message = update.get("message") or update.get("channel_post")
    if not message:
        store.put(_EFFECT_NAMESPACE, effect_key, {"status": "processed"})
        return
    configured_chat = os.getenv("TELEGRAM_CHAT_ID")
    actual_chat = str((message.get("chat") or {}).get("id", ""))
    if configured_chat and actual_chat != configured_chat:
        logger.warning("polling: rejected update from unconfigured chat")
        return
    configured_user = os.getenv("TELEGRAM_USER_ID")
    actual_user = str((message.get("from") or {}).get("id", ""))
    if configured_user and actual_user != configured_user:
        logger.warning("polling: rejected update from unconfigured user")
        return
    effect = store.get(_EFFECT_NAMESPACE, effect_key)
    if effect and effect.value.get("status") == "processed":
        return
    if effect and effect.value.get("status") == "attempting":
        raise RuntimeError(f"telegram update {effect_key} requires effect reconciliation")
    store.put(_EFFECT_NAMESPACE, effect_key, {
        "status": "attempting", "started_at": datetime.now(timezone.utc).isoformat()
    })

    reply_to = message.get("reply_to_message")
    text = (message.get("text") or "").strip()

    if reply_to:
        reply_msg_id = str(reply_to["message_id"])
        resume_item = store.get(("weekly_intel", "pending_resume_map"), reply_msg_id)

        if resume_item:
            _handle_approval_reply(reply_msg_id, text, resume_item.value, store)
        else:
            feedback_router.handle_feedback(message)
    else:
        _queue_adhoc(text, store, key=f"telegram-update:{effect_key}")
    store.put(_EFFECT_NAMESPACE, effect_key, {
        "status": "processed", "processed_at": datetime.now(timezone.utc).isoformat()
    })


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
        handle_approval(result, thread_id, run_id)
    else:
        handle_rejection(result, run_id)

    store.delete(("weekly_intel", "pending_resume_map"), reply_msg_id)
    logger.info(f"polling: resolved {record['proposal_id']} → {decision}")


def _queue_adhoc(text: str, store, key: str | None = None) -> None:
    if not text:
        return
    key = key or str(uuid.uuid4())
    store.put(
        ("weekly_intel", "adhoc_queue"),
        key,
        {
            "text": text,
            "queued_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    logger.info(f"polling: queued ad-hoc message (key={key})")
