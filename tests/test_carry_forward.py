"""
sunday/carry_forward.py -- new module. Covers the eligibility logic
(unchecked-or-no-row AND not-already-carried -> carry; checked=true ->
never carry; already-carried -> never carry twice), the prior-Sunday-run
lookup, and the section filter that excludes Existing Project Work
items. get_store()/get_connection_pool() both mocked so this stays
fully offline -- no real DB/store call.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

import sunday.carry_forward as carry_forward_mod
from sunday.carry_forward import get_carry_forward_items


class _Item:
    def __init__(self, value, key=None):
        self.value = value
        self.key = key or value.get("url")


class _FakeStore:
    def __init__(self, run_history=None, digest_item_map=None, carry_log=None):
        self._data = {
            ("weekly_intel", "run_history"): run_history or [],
            ("weekly_intel", "digest_item_map"): digest_item_map or [],
            ("weekly_intel", "carry_forward_log"): carry_log or [],
        }
        self.put_calls: list[tuple] = []

    def search(self, namespace, limit=1000):
        return [_Item(v) for v in self._data.get(namespace, [])]

    def put(self, namespace, key, value):
        self.put_calls.append((namespace, key, value))
        self._data.setdefault(namespace, []).append(value)


def _run_history_entry(run_id, path="sunday", status="success", finished_at="2026-07-12T00:00:00+00:00"):
    return {"run_id": run_id, "path": path, "status": status, "finished_at": finished_at}


def _digest_entry(run_id, items):
    return {"run_id": run_id, "items": items}


def _reading_item(url, title="A title", text="body", tags=None, reasoning="r"):
    return {"url": url, "title": title, "text": text, "tags": tags or ["agentic-engineering"], "reasoning": reasoning, "section": "reading"}


def _course_item(url, title="A course"):
    return {"url": url, "title": title, "text": "body", "tags": ["course"], "reasoning": "r", "section": "courses"}


def _project_item(url):
    return {"url": url, "title": "Project card", "text": "", "tags": [], "reasoning": "r", "section": "existing_project_work"}


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def execute(self, query, params):
        pass
    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def cursor(self):
        return _FakeCursor(self._rows)


class _FakePool:
    def __init__(self, rows):
        self._rows = rows
    def connection(self):
        return _FakeConn(self._rows)


def _mock_db(rows):
    return patch.object(carry_forward_mod, "get_connection_pool", return_value=_FakePool(rows))


def test_no_prior_sunday_run_returns_empty():
    fake_store = _FakeStore(run_history=[])
    with patch.object(carry_forward_mod, "get_store", return_value=fake_store):
        result = get_carry_forward_items("run-current")
    assert result == []


def test_unchecked_item_with_no_completion_row_is_carried():
    """No row at all in companion_item_completions == never interacted
    with == treated as unchecked, per the explicit spec."""
    fake_store = _FakeStore(
        run_history=[_run_history_entry("run-prior")],
        digest_item_map=[_digest_entry("run-prior", {1: _reading_item("https://a.com/1")})],
    )
    with patch.object(carry_forward_mod, "get_store", return_value=fake_store), _mock_db([]):
        result = get_carry_forward_items("run-current")
    assert len(result) == 1
    assert result[0]["url"] == "https://a.com/1"
    assert result[0]["matched_card_id"] is None
    assert result[0]["classification"] == "plan_item"


def test_explicitly_unchecked_item_is_carried():
    fake_store = _FakeStore(
        run_history=[_run_history_entry("run-prior")],
        digest_item_map=[_digest_entry("run-prior", {1: _reading_item("https://a.com/1")})],
    )
    with patch.object(carry_forward_mod, "get_store", return_value=fake_store), \
         _mock_db([{"url": "https://a.com/1", "checked": False}]):
        result = get_carry_forward_items("run-current")
    assert len(result) == 1


def test_checked_true_item_never_carried():
    fake_store = _FakeStore(
        run_history=[_run_history_entry("run-prior")],
        digest_item_map=[_digest_entry("run-prior", {1: _reading_item("https://a.com/1")})],
    )
    with patch.object(carry_forward_mod, "get_store", return_value=fake_store), \
         _mock_db([{"url": "https://a.com/1", "checked": True}]):
        result = get_carry_forward_items("run-current")
    assert result == []


def test_checked_true_excluded_selectively_alongside_an_eligible_item():
    """Stronger than the single-item case above: with a checked=true item
    AND an eligible (unchecked) item in the SAME call, confirms the
    checked=true one is selectively excluded by the eligibility
    condition itself -- not just an incidentally empty result."""
    fake_store = _FakeStore(
        run_history=[_run_history_entry("run-prior")],
        digest_item_map=[_digest_entry("run-prior", {
            1: _reading_item("https://checked.com/1", title="Checked item"),
            2: _reading_item("https://unchecked.com/1", title="Unchecked item"),
        })],
    )
    with patch.object(carry_forward_mod, "get_store", return_value=fake_store), \
         _mock_db([{"url": "https://checked.com/1", "checked": True}]):
        # "https://unchecked.com/1" deliberately has NO row in the mocked
        # DB result -- both "checked=true" and "no row at all" are
        # exercised in one call, at the same time.
        result = get_carry_forward_items("run-current")

    result_urls = {r["url"] for r in result}
    assert result_urls == {"https://unchecked.com/1"}
    assert "https://checked.com/1" not in result_urls


def test_already_carried_item_not_carried_again():
    fake_store = _FakeStore(
        run_history=[_run_history_entry("run-prior")],
        digest_item_map=[_digest_entry("run-prior", {1: _reading_item("https://a.com/1")})],
        carry_log=[{"url": "https://a.com/1", "carried_in_run_id": "run-prior", "carried_at": "2026-07-12T00:00:00+00:00"}],
    )
    with patch.object(carry_forward_mod, "get_store", return_value=fake_store), _mock_db([]):
        result = get_carry_forward_items("run-current")
    assert result == []


def test_existing_project_work_items_excluded_from_candidates():
    """Trello-tracked items are out of scope for this mechanism entirely
    -- confirmed via the section field, not inferred."""
    fake_store = _FakeStore(
        run_history=[_run_history_entry("run-prior")],
        digest_item_map=[_digest_entry("run-prior", {1: _project_item("https://trello.com/c/xyz")})],
    )
    with patch.object(carry_forward_mod, "get_store", return_value=fake_store), _mock_db([]):
        result = get_carry_forward_items("run-current")
    assert result == []


def test_course_item_preserves_course_tag_when_carried():
    fake_store = _FakeStore(
        run_history=[_run_history_entry("run-prior")],
        digest_item_map=[_digest_entry("run-prior", {1: _course_item("https://b.com/1")})],
    )
    with patch.object(carry_forward_mod, "get_store", return_value=fake_store), _mock_db([]):
        result = get_carry_forward_items("run-current")
    assert result[0]["tags"] == ["course"]


def test_carried_item_logged_to_carry_forward_log():
    fake_store = _FakeStore(
        run_history=[_run_history_entry("run-prior")],
        digest_item_map=[_digest_entry("run-prior", {1: _reading_item("https://a.com/1")})],
    )
    with patch.object(carry_forward_mod, "get_store", return_value=fake_store), _mock_db([]):
        get_carry_forward_items("run-current")

    log_calls = [c for c in fake_store.put_calls if c[0] == ("weekly_intel", "carry_forward_log")]
    assert len(log_calls) == 1
    namespace, key, value = log_calls[0]
    assert key == "https://a.com/1"
    assert value["carried_in_run_id"] == "run-current"


def test_picks_most_recent_of_multiple_prior_sunday_runs():
    fake_store = _FakeStore(
        run_history=[
            _run_history_entry("run-old", finished_at="2026-07-05T00:00:00+00:00"),
            _run_history_entry("run-new", finished_at="2026-07-12T00:00:00+00:00"),
        ],
        digest_item_map=[
            _digest_entry("run-old", {1: _reading_item("https://old.com/1")}),
            _digest_entry("run-new", {1: _reading_item("https://new.com/1")}),
        ],
    )
    with patch.object(carry_forward_mod, "get_store", return_value=fake_store), _mock_db([]):
        result = get_carry_forward_items("run-current")
    assert [r["url"] for r in result] == ["https://new.com/1"]


def test_ignores_non_sunday_run_history_entries():
    fake_store = _FakeStore(
        run_history=[_run_history_entry("run-daily", path="daily")],
        digest_item_map=[_digest_entry("run-daily", {1: _reading_item("https://a.com/1")})],
    )
    with patch.object(carry_forward_mod, "get_store", return_value=fake_store), _mock_db([]):
        result = get_carry_forward_items("run-current")
    assert result == []


def test_ignores_failed_run_history_entries():
    fake_store = _FakeStore(
        run_history=[_run_history_entry("run-failed", status="failed")],
        digest_item_map=[_digest_entry("run-failed", {1: _reading_item("https://a.com/1")})],
    )
    with patch.object(carry_forward_mod, "get_store", return_value=fake_store), _mock_db([]):
        result = get_carry_forward_items("run-current")
    assert result == []
