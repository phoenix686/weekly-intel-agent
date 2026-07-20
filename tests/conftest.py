import os

# These files used to live in scripts/, run manually via
# `uv run --env-file .env python scripts/test_X.py` against real
# infrastructure (live Postgres/checkpointer, real Trello/Anthropic API
# calls) -- never meant to be collected by a bare `pytest`/`pytest tests/`
# invocation. Moved into tests/ (portfolio cleanup pass) so they live
# next to what they cover, but a plain `pytest tests/` must stay fast,
# free, and offline: several of these files make real API calls at
# *import time* (module-level code, not inside a test function), so
# pytest's default collection alone -- before running a single test --
# was taking 2+ minutes and incurring real Anthropic costs, and 5 of
# them error out during collection entirely (stale assumptions about
# live external state), which aborts the whole pytest session before any
# of the ~260 real offline unit tests get a chance to run.
#
# Ignored from default collection, not deleted -- still runnable
# explicitly and intentionally: `pytest tests/test_sunday_approval.py`
# or `uv run --env-file .env python tests/test_sunday_approval.py`.
collect_ignore = [
    "test_approval_outcome_log_live_roundtrip.py",
    "test_checkpointer.py",
    "test_classification_log_live_roundtrip.py",
    "test_classify.py",
    "test_classify_synthetic.py",
    "test_companion_store_roundtrip.py",
    "test_fan_in.py",
    "test_interrupt.py",
    "test_interrupt_multi.py",
    "test_item_feedback_logging_roundtrip.py",
    "test_multi_proposal_own_threads.py",
    "test_pending_resume_map_roundtrip.py",
    "test_per_proposal_thread.py",
    "test_prompt.py",
    "test_same_day_nudge_roundtrip.py",
    "test_semantic_dedup_live_roundtrip.py",
    "test_sunday_approval.py",
    "test_sunday_rewrite_live_roundtrip.py",
    "test_taste_prefilter_live_roundtrip.py",
    "test_trello_checklist.py",
    "test_trello_write.py",
    "test_update_profile_rejections.py",
]
