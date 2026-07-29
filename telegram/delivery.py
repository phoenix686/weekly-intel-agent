"""Idempotent Telegram delivery with commit-after-acknowledgement semantics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

DELIVERY_LEDGER_NAMESPACE = ("weekly_intel", "delivery_ledger")
DIGEST_MAP_NAMESPACE = ("weekly_intel", "digest_item_map")
TELEGRAM_SAFE_CHARS = 3_800
_NUMBERED_ITEM_RE = re.compile(r"(?m)^(\d+)\.\s")


@dataclass(frozen=True)
class DeliveryReceipt:
    pipeline: str
    delivery_date: str
    message_ids: tuple[int, ...]
    delivered_urls: tuple[str, ...]
    reused: bool = False


def split_html_message(text: str, max_chars: int = TELEGRAM_SAFE_CHARS) -> list[str]:
    """Split at paragraph seams so item markup stays intact."""
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(paragraph) > max_chars:
            split_at = paragraph.rfind("\n", 0, max_chars)
            if split_at <= 0:
                split_at = max_chars
            chunks.append(paragraph[:split_at])
            paragraph = paragraph[split_at:].lstrip("\n")
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def _message_id(response: dict[str, Any]) -> int:
    try:
        return int(response["result"]["message_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Telegram acknowledgement missing message_id: {response}") from exc


def _item_subset(chunk: str, item_map: dict[int, dict]) -> dict[int, dict]:
    numbers = {int(value) for value in _NUMBERED_ITEM_RE.findall(chunk)}
    return {number: item_map[number] for number in sorted(numbers) if number in item_map}


def deliver_telegram(
    *,
    pipeline: str,
    run_id: str,
    text: str,
    item_map: dict[int, dict],
    store: Any,
    send: Callable[[str], dict[str, Any]],
    mark_seen: Callable[[list[str]], None],
    delivery_date: str | None = None,
    dry_run: bool = False,
) -> DeliveryReceipt:
    date_value = delivery_date or datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    ledger_key = f"{pipeline}:{date_value}" if not dry_run else f"{pipeline}:dry-run:{run_id}"
    existing_item = store.get(DELIVERY_LEDGER_NAMESPACE, ledger_key)
    existing = existing_item.value if existing_item else None

    if existing and existing.get("status") == "delivered":
        return DeliveryReceipt(
            pipeline=pipeline,
            delivery_date=date_value,
            message_ids=tuple(existing.get("message_ids", [])),
            delivered_urls=tuple(existing.get("delivered_urls", [])),
            reused=True,
        )

    chunks = split_html_message(text)
    message_ids = list(existing.get("message_ids", [])) if existing else []
    chunks_sent = int(existing.get("chunks_sent", 0)) if existing else 0
    delivered_urls = list(
        dict.fromkeys(
            url
            for item in item_map.values()
            for url in ([item["url"]] if item.get("url") else []) + [
                source["url"] for source in item.get("supporting_sources", []) if source.get("url")
            ]
        )
    )
    ledger = {
        "pipeline": pipeline,
        "run_id": run_id,
        "delivery_date": date_value,
        "status": "in_progress",
        "chunks_total": len(chunks),
        "chunks_sent": chunks_sent,
        "message_ids": message_ids,
        "delivered_urls": delivered_urls,
        "updated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
    }
    store.put(DELIVERY_LEDGER_NAMESPACE, ledger_key, ledger)

    if not existing or existing.get("status") != "delivered_pending_commit":
        for chunk_index, chunk in enumerate(chunks[chunks_sent:], start=chunks_sent):
            response = send(chunk)
            message_id = _message_id(response)
            message_ids.append(message_id)
            subset = _item_subset(chunk, item_map)
            if subset:
                store.put(
                    DIGEST_MAP_NAMESPACE,
                    str(message_id),
                    {
                        "run_id": run_id,
                        "pipeline": pipeline,
                        "delivery_date": date_value,
                        "expires_at": (datetime.now(ZoneInfo("UTC")) + timedelta(days=14)).isoformat(),
                        "items": subset,
                    },
                )
            ledger.update(
                {
                    "chunks_sent": chunk_index + 1,
                    "message_ids": list(message_ids),
                    "updated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
                }
            )
            store.put(DELIVERY_LEDGER_NAMESPACE, ledger_key, dict(ledger))

        ledger["status"] = "delivered_pending_commit"
        store.put(DELIVERY_LEDGER_NAMESPACE, ledger_key, dict(ledger))

    if not dry_run and delivered_urls:
        mark_seen(delivered_urls)

    ledger.update(
        {
            "status": "delivered",
            "delivered_at": datetime.now(ZoneInfo("UTC")).isoformat(),
            "message_ids": list(message_ids),
        }
    )
    store.put(DELIVERY_LEDGER_NAMESPACE, ledger_key, ledger)
    return DeliveryReceipt(
        pipeline=pipeline,
        delivery_date=date_value,
        message_ids=tuple(message_ids),
        delivered_urls=tuple(delivered_urls),
    )
