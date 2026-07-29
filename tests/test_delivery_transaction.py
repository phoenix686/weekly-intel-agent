from unittest.mock import Mock

import pytest

from telegram.delivery import deliver_telegram, split_html_message


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


def test_message_splitting_never_exceeds_safe_size():
    text = "\n\n".join(
        f"{index}. <a href=\"https://example.com/{index}\">{'x' * 700}</a>"
        for index in range(1, 11)
    )

    chunks = split_html_message(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= 3_800 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")
