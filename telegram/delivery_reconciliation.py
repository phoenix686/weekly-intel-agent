"""Operator resolution for a Telegram chunk with an ambiguous acknowledgement."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from telegram.delivery import (
    DELIVERY_LEDGER_NAMESPACE,
    DIGEST_MAP_NAMESPACE,
    _item_subset,
    split_html_message,
)


def reconcile_delivery(
    store: Any,
    ledger_key: str,
    resolution: str,
    *,
    message_id: int | None = None,
) -> None:
    """Resolve an ambiguous chunk as not sent or accepted by Telegram."""
    if resolution not in {"not_sent", "accepted"}:
        raise ValueError("resolution must be not_sent or accepted")
    item = store.get(DELIVERY_LEDGER_NAMESPACE, ledger_key)
    if not item:
        raise KeyError(f"delivery ledger not found: {ledger_key}")
    ledger = dict(item.value)
    chunk_index = ledger.get("attempting_chunk_index")
    if chunk_index is None:
        raise ValueError(f"{ledger_key} has no ambiguous Telegram attempt")
    if chunk_index != int(ledger.get("chunks_sent", 0)):
        raise ValueError("ambiguous chunk is not the next unacknowledged chunk")

    if resolution == "not_sent":
        ledger["attempting_chunk_index"] = None
        ledger["attempting_chunk_sha256"] = None
    else:
        if message_id is None:
            raise ValueError("message_id is required when resolution is accepted")
        payload = ledger.get("domain_commit_payload") or {}
        chunks = split_html_message(payload.get("text", ""))
        if chunk_index >= len(chunks):
            raise ValueError("ambiguous chunk index is outside the persisted payload")
        message_ids = list(ledger.get("message_ids", []))
        if message_id not in message_ids:
            message_ids.append(message_id)
        ledger["message_ids"] = message_ids
        ledger["chunks_sent"] = chunk_index + 1
        ledger["attempting_chunk_index"] = None
        ledger["attempting_chunk_sha256"] = None
        subset = _item_subset(chunks[chunk_index], payload.get("item_map", {}))
        if subset:
            store.put(
                DIGEST_MAP_NAMESPACE,
                str(message_id),
                {
                    "run_id": ledger["run_id"],
                    "pipeline": ledger["pipeline"],
                    "delivery_date": ledger["delivery_date"],
                    "expires_at": (
                        datetime.now(ZoneInfo("UTC")) + timedelta(days=14)
                    ).isoformat(),
                    "items": subset,
                },
            )
        if ledger["chunks_sent"] == ledger.get("chunks_total"):
            ledger["status"] = "delivered_pending_commit"

    ledger["reconciled_at"] = datetime.now(ZoneInfo("UTC")).isoformat()
    ledger["reconciliation_resolution"] = resolution
    store.put(DELIVERY_LEDGER_NAMESPACE, ledger_key, ledger)
