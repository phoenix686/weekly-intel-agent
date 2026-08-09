import json

import pytest

from telegram.delivery import DELIVERY_LEDGER_NAMESPACE, DIGEST_MAP_NAMESPACE
from telegram.delivery_reconciliation import reconcile_delivery
from tests.test_delivery_transaction import _Store


def _ambiguous_ledger():
    return json.loads(json.dumps({
        "pipeline": "daily",
        "run_id": "run-1",
        "delivery_date": "2026-07-29",
        "status": "in_progress",
        "chunks_total": 1,
        "chunks_sent": 0,
        "message_ids": [],
        "domain_commit_payload": {
            "text": "1. Story",
            "item_map": {1: {"url": "https://example.com/story"}},
        },
        "attempting_chunk_index": 0,
        "attempting_chunk_sha256": "hash",
    }))


def test_reconcile_not_sent_allows_the_same_chunk_to_be_retried():
    store = _Store()
    store.put(DELIVERY_LEDGER_NAMESPACE, "daily:2026-07-29", _ambiguous_ledger())

    reconcile_delivery(store, "daily:2026-07-29", "not_sent")

    ledger = store.get(DELIVERY_LEDGER_NAMESPACE, "daily:2026-07-29").value
    assert ledger["status"] == "in_progress"
    assert ledger["chunks_sent"] == 0
    assert ledger["attempting_chunk_index"] is None


def test_reconcile_accepted_records_message_and_reply_map():
    store = _Store()
    store.put(DELIVERY_LEDGER_NAMESPACE, "daily:2026-07-29", _ambiguous_ledger())

    reconcile_delivery(
        store, "daily:2026-07-29", "accepted", message_id=42
    )

    ledger = store.get(DELIVERY_LEDGER_NAMESPACE, "daily:2026-07-29").value
    assert ledger["status"] == "delivered_pending_commit"
    assert ledger["chunks_sent"] == 1
    assert ledger["message_ids"] == [42]
    reply_map = store.get(DIGEST_MAP_NAMESPACE, "42").value
    assert reply_map["run_id"] == "run-1"
    assert reply_map["items"][1]["url"] == "https://example.com/story"


def test_reconcile_accepted_requires_message_id():
    store = _Store()
    store.put(DELIVERY_LEDGER_NAMESPACE, "daily:2026-07-29", _ambiguous_ledger())

    with pytest.raises(ValueError, match="message_id"):
        reconcile_delivery(store, "daily:2026-07-29", "accepted")


def test_reconcile_accepted_is_idempotent_after_reply_map_write():
    class _FailFinalLedgerWriteOnce(_Store):
        def __init__(self):
            super().__init__()
            self.fail_once = True

        def put(self, namespace, key, value):
            if (
                namespace == DELIVERY_LEDGER_NAMESPACE
                and value.get("reconciliation_resolution") == "accepted"
                and self.fail_once
            ):
                self.fail_once = False
                raise RuntimeError("ledger unavailable")
            super().put(namespace, key, value)

    store = _FailFinalLedgerWriteOnce()
    store.put(DELIVERY_LEDGER_NAMESPACE, "daily:2026-07-29", _ambiguous_ledger())

    with pytest.raises(RuntimeError, match="ledger unavailable"):
        reconcile_delivery(
            store, "daily:2026-07-29", "accepted", message_id=42
        )
    reconcile_delivery(
        store, "daily:2026-07-29", "accepted", message_id=42
    )

    ledger = store.get(DELIVERY_LEDGER_NAMESPACE, "daily:2026-07-29").value
    assert ledger["message_ids"] == [42]
    reply_map = store.get(DIGEST_MAP_NAMESPACE, "42").value
    assert reply_map["items"][1]["url"] == "https://example.com/story"
