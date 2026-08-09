import json
from unittest.mock import Mock

import pytest

from telegram.delivery import (
    DELIVERY_LEDGER_NAMESPACE,
    DeliveryReconciliationRequired,
    deliver_telegram,
    mark_domain_commit_complete,
    split_html_message,
)


class _Item:
    def __init__(self, value):
        self.value = value


class _Store:
    def __init__(self):
        self.data = {}

    def get(self, namespace, key):
        value = self.data.get((namespace, key))
        return _Item(value) if value is not None else None

    def put(self, namespace, key, value):
        self.data[(namespace, key)] = value


def test_failed_delivery_does_not_mark_articles_seen():
    store = _Store()
    mark_seen = Mock()

    with pytest.raises(RuntimeError, match="telegram unavailable"):
        deliver_telegram(
            pipeline="daily",
            run_id="run-1",
            text="Digest\n\n1. First story",
            item_map={1: {"url": "https://example.com/1"}},
            store=store,
            send=lambda _: (_ for _ in ()).throw(RuntimeError("telegram unavailable")),
            mark_seen=mark_seen,
            delivery_date="2026-07-29",
        )

    mark_seen.assert_not_called()
    ledger = store.get(("weekly_intel", "delivery_ledger"), "daily:2026-07-29")
    assert ledger.value["status"] == "in_progress"

    with pytest.raises(DeliveryReconciliationRequired):
        deliver_telegram(
            pipeline="daily", run_id="run-1", text="Digest\n\n1. First story",
            item_map={1: {"url": "https://example.com/1"}}, store=store,
            send=Mock(), mark_seen=mark_seen, delivery_date="2026-07-29",
        )


def test_successful_delivery_commits_only_rendered_articles():
    store = _Store()
    mark_seen = Mock()
    send = Mock(return_value={"ok": True, "result": {"message_id": 123}})

    receipt = deliver_telegram(
        pipeline="daily",
        run_id="run-1",
        text="Digest\n\n1. First story",
        item_map={1: {"url": "https://example.com/1"}},
        store=store,
        send=send,
        mark_seen=mark_seen,
        delivery_date="2026-07-29",
    )

    assert receipt.message_ids == (123,)
    mark_seen.assert_called_once_with(["https://example.com/1"])
    ledger = store.get(("weekly_intel", "delivery_ledger"), "daily:2026-07-29")
    assert ledger.value["status"] == "delivered"


def test_delivered_date_is_idempotent():
    store = _Store()
    send = Mock(return_value={"ok": True, "result": {"message_id": 123}})
    mark_seen = Mock()
    kwargs = {
        "pipeline": "daily",
        "run_id": "run-1",
        "text": "Digest\n\n1. First story",
        "item_map": {1: {"url": "https://example.com/1"}},
        "store": store,
        "send": send,
        "mark_seen": mark_seen,
        "delivery_date": "2026-07-29",
    }

    deliver_telegram(**kwargs)
    second = deliver_telegram(**kwargs)

    assert second.reused is True
    assert send.call_count == 1
    assert mark_seen.call_count == 1


def test_domain_commit_is_a_separate_resumable_phase():
    store = _Store()
    receipt = deliver_telegram(
        pipeline="daily", run_id="same-run", text="Digest",
        item_map={}, store=store,
        send=Mock(return_value={"result": {"message_id": 9}}),
        mark_seen=Mock(), delivery_date="2026-07-29",
    )
    ledger = store.get(("weekly_intel", "delivery_ledger"), "daily:2026-07-29")
    assert ledger.value["domain_commit_status"] == "pending"
    mark_domain_commit_complete(store, receipt)
    assert store.get(
        ("weekly_intel", "delivery_ledger"), "daily:2026-07-29"
    ).value["domain_commit_status"] == "committed"


def test_message_splitting_never_exceeds_safe_size():
    text = "\n\n".join(
        f"{index}. <a href=\"https://example.com/{index}\">{'x' * 700}</a>"
        for index in range(1, 11)
    )

    chunks = split_html_message(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= 3_800 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_fresh_run_finishes_original_pending_domain_commit_without_resending():
    store = _Store()
    old_payload = {
        "run_id": "old-run",
        "text": "Old digest\n\n1. Old story",
        "item_map": {1: {"url": "https://example.com/old"}},
        "generated_at": "2026-07-29T01:00:00+00:00",
    }
    old_payload = json.loads(json.dumps(old_payload))
    store.put(
        DELIVERY_LEDGER_NAMESPACE,
        "daily:2026-07-29",
        {
            "pipeline": "daily",
            "run_id": "old-run",
            "delivery_date": "2026-07-29",
            "status": "delivered_pending_commit",
            "chunks_total": 1,
            "chunks_sent": 1,
            "message_ids": [10],
            "delivered_urls": ["https://example.com/old"],
            "domain_commit_status": "pending",
            "domain_commit_payload": old_payload,
        },
    )
    send = Mock()
    mark_seen = Mock()

    receipt = deliver_telegram(
        pipeline="daily",
        run_id="new-run",
        text="New digest\n\n1. New story",
        item_map={1: {"url": "https://example.com/new"}},
        store=store,
        send=send,
        mark_seen=mark_seen,
        delivery_date="2026-07-29",
        domain_commit_payload={
            "run_id": "new-run",
            "text": "New digest\n\n1. New story",
            "item_map": {1: {"url": "https://example.com/new"}},
        },
    )

    send.assert_not_called()
    mark_seen.assert_called_once_with(["https://example.com/old"])
    assert receipt.run_id == "old-run"
    assert receipt.domain_commit_payload == old_payload
    assert receipt.message_ids == (10,)
    ledger = store.get(DELIVERY_LEDGER_NAMESPACE, "daily:2026-07-29").value
    assert ledger["run_id"] == "old-run"
    assert ledger["status"] == "delivered"


def test_fresh_run_resumes_original_partial_payload_not_new_digest():
    store = _Store()
    first = "1. " + ("a" * 3_790)
    second = "2. old second story"
    old_text = f"{first}\n\n{second}"
    old_payload = {
        "run_id": "old-run",
        "text": old_text,
        "item_map": {
            1: {"url": "https://example.com/old-1"},
            2: {"url": "https://example.com/old-2"},
        },
        "generated_at": "2026-07-29T01:00:00+00:00",
    }
    old_payload = json.loads(json.dumps(old_payload))
    store.put(
        DELIVERY_LEDGER_NAMESPACE,
        "daily:2026-07-29",
        {
            "pipeline": "daily",
            "run_id": "old-run",
            "delivery_date": "2026-07-29",
            "status": "in_progress",
            "chunks_total": 2,
            "chunks_sent": 1,
            "message_ids": [10],
            "delivered_urls": [
                "https://example.com/old-1",
                "https://example.com/old-2",
            ],
            "domain_commit_status": "pending",
            "domain_commit_payload": old_payload,
            "attempting_chunk_index": None,
        },
    )
    send = Mock(return_value={"result": {"message_id": 11}})
    mark_seen = Mock()

    receipt = deliver_telegram(
        pipeline="daily",
        run_id="new-run",
        text="1. new digest",
        item_map={1: {"url": "https://example.com/new"}},
        store=store,
        send=send,
        mark_seen=mark_seen,
        delivery_date="2026-07-29",
        domain_commit_payload={
            "run_id": "new-run",
            "text": "1. new digest",
            "item_map": {1: {"url": "https://example.com/new"}},
        },
    )

    send.assert_called_once_with(second)
    mark_seen.assert_called_once_with(
        ["https://example.com/old-1", "https://example.com/old-2"]
    )
    assert receipt.run_id == "old-run"
    assert receipt.message_ids == (10, 11)
    assert receipt.domain_commit_payload == old_payload
    reply_map = store.get(
        ("weekly_intel", "digest_item_map"), "11"
    ).value
    assert reply_map["items"][2]["url"] == "https://example.com/old-2"
