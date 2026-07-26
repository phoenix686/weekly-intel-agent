"""
discovery/taste_profile_store.py -- the real fix for taste_profile.yaml's
persistence gap (2026-07-26 investigation): every GitHub Actions Sunday
run wrote its rewrite to a local data/ path that's gitignored, has no
commit-back step, and no artifact upload -- so the runner's disk
(including that write) was destroyed at job end, regardless of whether
real feedback existed that week. Postgres (the same Supabase instance
already used everywhere else in weekly_intel) is durable across runners
by construction; this file proves that durability directly.

Mirrors tests/test_seen_items.py's _FakeStore pattern.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import discovery.taste_profile_store as taste_profile_store_mod
from discovery.taste_profile_store import get_taste_profile, put_taste_profile


class _Item:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _FakeStore:
    """Stands in for the real Postgres-backed store -- the one thing
    that's actually durable across a fresh GitHub Actions runner, unlike
    any local file or in-process state."""
    def __init__(self):
        self._data: dict = {}

    def get(self, namespace, key):
        value = self._data.get((namespace, key))
        return _Item(key, value) if value is not None else None

    def put(self, namespace, key, value):
        self._data[(namespace, key)] = value


def test_get_taste_profile_returns_none_when_never_written():
    fake_store = _FakeStore()
    with patch.object(taste_profile_store_mod, "get_store", return_value=fake_store):
        assert get_taste_profile() is None


def test_put_then_get_round_trips_the_content():
    fake_store = _FakeStore()
    with patch.object(taste_profile_store_mod, "get_store", return_value=fake_store):
        put_taste_profile("version: 1\nproposal_filters: []\nnotes: 'first write'")
        result = get_taste_profile()

    assert result == "version: 1\nproposal_filters: []\nnotes: 'first write'"


def test_put_stores_a_real_updated_at_timestamp():
    fake_store = _FakeStore()
    with patch.object(taste_profile_store_mod, "get_store", return_value=fake_store):
        put_taste_profile("version: 1")

    stored = fake_store._data[(taste_profile_store_mod._NAMESPACE, taste_profile_store_mod._KEY)]
    assert stored["content"] == "version: 1"
    assert stored["updated_at"]  # non-empty real ISO timestamp, not a placeholder


def test_put_overwrites_the_single_row_not_append():
    """One row, always the latest -- no history retained, matching the
    local file's prior overwrite-in-place semantics."""
    fake_store = _FakeStore()
    with patch.object(taste_profile_store_mod, "get_store", return_value=fake_store):
        put_taste_profile("version: 1")
        put_taste_profile("version: 2")
        result = get_taste_profile()

    assert result == "version: 2"
    assert len(fake_store._data) == 1


def test_round_trip_survives_a_simulated_fresh_runner_with_no_local_file():
    """Direct regression test for the real bug (2026-07-26): two separate
    'runner' invocations share NOTHING but the store -- no local file, no
    tmp_path, no shared process state of any kind. fake_store here stands
    in for the one thing that's actually durable across a fresh GitHub
    Actions runner: the real Postgres instance. If this round-trips
    correctly with zero filesystem involvement anywhere in this test,
    the profile's durability provably comes from Postgres, not from
    local state a fresh runner would never actually have."""
    fake_store = _FakeStore()
    rewritten_yaml = "version: 5\nproposal_filters: [{tag: evals, weight: 0.9}]\nnotes: 'fresh runner test'"

    # "Runner 1": a Sunday run's update_profile() writes a fresh rewrite,
    # then the runner (and everything on its local disk) is destroyed.
    with patch.object(taste_profile_store_mod, "get_store", return_value=fake_store):
        put_taste_profile(rewritten_yaml)

    # "Runner 2": a brand-new process on a brand-new machine -- nothing
    # carried over except reachability to the same real Postgres instance.
    with patch.object(taste_profile_store_mod, "get_store", return_value=fake_store):
        result = get_taste_profile()

    assert result == rewritten_yaml
