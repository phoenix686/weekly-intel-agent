"""
Durable, single-row source of truth for taste_profile.yaml's content, in
Postgres -- 2026-07-26 fix. Real bug found investigating why data/
taste_profile.yaml hadn't regenerated since 2026-07-20 despite real
feedback and real Saturday runs since: data/ is gitignored (personal-taste
-derived content, same privacy class as the AgentMail inbox address and
Trello board ID already kept out of git), and saturday.yml has no
commit-back step and no artifact upload covering data/ -- so every real
rewrite a GitHub Actions runner produced was written to that runner's
disk and destroyed with it at job end. The local file was never really
a source of truth for any CI-triggered run; it only ever reflected
whoever's local machine last ran update_profile() directly.

Postgres (the same Supabase instance already used for the checkpointer
and every other weekly_intel namespace -- reused via saturday/
memory_store_config.py's get_store(), which itself wraps core/
connection_pool.py's get_connection_pool(), not a new client) is durable
across runners by construction. This makes it the real source of truth
going forward. The local file is now a read-only mirror for manual
inspection only (scripts/sync_taste_profile.py) -- never written back
to Postgres from the local copy.

One row, one key ("current") under ("weekly_intel", "taste_profile"):
{content: <full yaml text>, updated_at: <iso8601>}.

No langgraph imports.
"""

from __future__ import annotations

from datetime import datetime, timezone

from saturday.memory_store_config import get_store

_NAMESPACE = ("weekly_intel", "taste_profile")
_KEY = "current"


def get_taste_profile() -> str | None:
    """Real current profile YAML text, or None if never written (first
    run ever, or a fresh database)."""
    store = get_store()
    item = store.get(_NAMESPACE, _KEY)
    return item.value["content"] if item is not None else None


def put_taste_profile(content: str) -> None:
    """Overwrites the single current-profile row with fresh content and
    a fresh updated_at timestamp -- one row, always the latest, no
    history retained (matches the local file's prior overwrite-in-place
    semantics; plan_history/scored_items_log already cover durable
    per-run history elsewhere for anything that needs it)."""
    store = get_store()
    store.put(_NAMESPACE, _KEY, {
        "content": content,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
