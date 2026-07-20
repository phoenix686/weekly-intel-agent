"""
Capped, one-time-only carry-forward for unfinished Reading & Learning /
Courses items, reading from the real `companion_item_completions` table
(url TEXT PK, checked BOOLEAN, run_id TEXT, updated_at TIMESTAMPTZ) --
written externally by companion_writer (a separate app), read-only from
this side (SELECT only, no INSERT/UPDATE/DELETE against that table from
this codebase).

Lifecycle, per url:
  week N:   item first shown (new discovery, real scoring). Not carried.
  week N+1: still unchecked in companion_item_completions (or no row at
            all -- never interacted with) AND never carried before ->
            carried forward into this week's Reading/Courses, using its
            ALREADY-SCORED data (no re-score, no re-Haiku-charge -- see
            below for why this can never happen). Logged to
            ("weekly_intel", "carry_forward_log") the same call.
  week N+2: still unchecked, but now IS in carry_forward_log (logged in
            week N+1) -> NOT carried again. Dropped permanently, no
            exceptions -- Pooja re-adds it herself via ad-hoc input if
            she still wants it.
  (any week): checked=true in companion_item_completions -> never
            carried, at any point in the lifecycle above.

Why a carried item can never incur a duplicate Haiku charge or get
blocked by seen_items: get_carry_forward_items() is called from
sunday/nodes/assemble_plan.py, the LAST real node before the plan is
rendered. A carried item is built directly from last week's
digest_item_map entry (already-scored title/text/tags/reasoning) and
injected straight into this run's classified_items -- it never becomes
part of this run's raw_items/clustered_items, so it never reaches
discovery/nodes/cluster_dedupe.py's filter_unseen() or
discovery/nodes/score.py's score_node() at all, this run or any run.
There is no code path connecting this module to either.

"Last week's Reading/Courses items" is resolved via run_history (finding
the most recent completed Sunday run before this one) and that run's
digest_item_map entry, filtered by the "section" field added to
item_map entries (sunday/nodes/assemble_plan.py's format_plan()) --
without it, Existing Project Work items (Trello-tracked separately via
plan_history/card_movements, entirely out of scope here) couldn't be
reliably excluded from digest_item_map's otherwise-identically-shaped
entries.

No langgraph imports.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.connection_pool import get_connection_pool
from sunday.memory_store_config import get_store

logger = logging.getLogger(__name__)

_CARRY_LOG_NAMESPACE = ("weekly_intel", "carry_forward_log")
_CARRYABLE_SECTIONS = {"reading", "courses"}


def _find_prior_sunday_run_id(current_run_id: str) -> str | None:
    """Most recent COMPLETED ("success") Sunday run before this one, per
    run_history -- excludes the current run defensively (mirrors
    plan_history.get_most_recent_prior_entry's same precaution) and
    excludes anything not yet finished (a failed/in-progress run has no
    real digest_item_map entry worth trusting)."""
    store = get_store()
    entries = [item.value for item in store.search(("weekly_intel", "run_history"), limit=1000)]
    sunday_runs = [
        e for e in entries
        if e.get("path") == "sunday" and e.get("run_id") != current_run_id and e.get("status") == "success"
    ]
    if not sunday_runs:
        return None
    return max(sunday_runs, key=lambda e: e.get("finished_at") or "")["run_id"]


def _load_prior_reading_and_course_items(prior_run_id: str) -> list[dict]:
    """Reading & Learning + Courses items only, from the prior Sunday
    run's digest_item_map entry -- Existing Project Work items (section
    == "existing_project_work") are Trello-tracked separately and
    excluded here entirely."""
    store = get_store()
    entries = store.search(("weekly_intel", "digest_item_map"), limit=1000)
    for entry in entries:
        if entry.value.get("run_id") == prior_run_id:
            items = entry.value.get("items", {})
            return [item for item in items.values() if item.get("section") in _CARRYABLE_SECTIONS]
    return []


def _fetch_completion_status(urls: list[str]) -> dict[str, bool]:
    """SELECT-only against companion_item_completions. A url with no row
    at all is treated as unchecked by the caller (never interacted with
    == not done), not specially handled here."""
    if not urls:
        return {}
    pool = get_connection_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT url, checked FROM companion_item_completions WHERE url = ANY(%s)",
                (urls,),
            )
            return {row["url"]: row["checked"] for row in cur.fetchall()}


def _already_carried(urls: list[str]) -> set[str]:
    store = get_store()
    entries = store.search(_CARRY_LOG_NAMESPACE, limit=10000)
    carried_urls = {entry.key for entry in entries}
    return {url for url in urls if url in carried_urls}


def _log_carried(urls: list[str], run_id: str) -> None:
    if not urls:
        return
    store = get_store()
    now = datetime.now(timezone.utc).isoformat()
    for url in urls:
        store.put(_CARRY_LOG_NAMESPACE, url, {"url": url, "carried_in_run_id": run_id, "carried_at": now})


def get_carry_forward_items(current_run_id: str) -> list[dict]:
    """Returns classified_item-shaped dicts (classification="plan_item",
    matched_card_id=None, tags preserved from last week -- so a carried
    course item still lands back in Courses, not Reading & Learning) for
    every eligible carry-forward item. Records each returned url to
    carry_forward_log in the same call, so it can never be carried a
    second time regardless of what happens to it this week."""
    prior_run_id = _find_prior_sunday_run_id(current_run_id)
    if prior_run_id is None:
        logger.info(f"carry_forward: no prior completed Sunday run found (run={current_run_id})")
        return []

    candidates = _load_prior_reading_and_course_items(prior_run_id)
    if not candidates:
        logger.info(f"carry_forward: prior Sunday run {prior_run_id} had no Reading/Courses items (run={current_run_id})")
        return []

    urls = [c["url"] for c in candidates]
    completion_status = _fetch_completion_status(urls)
    already_carried = _already_carried(urls)

    eligible = [
        c for c in candidates
        if not completion_status.get(c["url"], False)  # unchecked OR no row at all -> unchecked
        and c["url"] not in already_carried
    ]

    carried_items = [
        {
            "url": c["url"], "title": c["title"], "text": c["text"],
            "reasoning": c["reasoning"], "classification": "plan_item",
            "proposal_type": None, "classification_reasoning": "carried forward, unfinished last week",
            "matched_card_id": None, "tags": c.get("tags", []),
        }
        for c in eligible
    ]

    _log_carried([c["url"] for c in carried_items], current_run_id)
    logger.info(
        f"carry_forward: {len(carried_items)}/{len(candidates)} prior Reading/Courses item(s) "
        f"carried forward from run {prior_run_id} (run={current_run_id})"
    )
    return carried_items
