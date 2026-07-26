"""
One-time migration (2026-07-18): backfills a seen_at timestamp onto every
seen_items entry written before this field existed. Existing entries carry
no real age signal (mark_seen() only ever wrote {"seen": True}), so
treating them as already-expired would delete real cross-run dedup
history on a guess -- the same mistake already flagged once this session.
Backfilling with "now" is the conservative choice: nothing gets wrongly
expired today, and the 35-day window (discovery/seen_items.py's
_WINDOW_DAYS) starts applying honestly from here going forward.

Run once, manually: uv run --env-file .env python scripts/backfill_seen_items_timestamp.py
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from langgraph.store.base import PutOp
from saturday.memory_store_config import get_store
from discovery.seen_items import _NAMESPACE

store = get_store()
before = store.search(_NAMESPACE, limit=10000)
print(f"BEFORE: {len(before)} total seen_items entries")

missing = [entry for entry in before if entry.value.get("seen_at") is None]
print(f"Entries missing seen_at (to be backfilled): {len(missing)}")

if missing:
    now = datetime.now(timezone.utc).isoformat()
    store.batch([
        PutOp(_NAMESPACE, entry.key, {**entry.value, "seen_at": now})
        for entry in missing
    ])

after = store.search(_NAMESPACE, limit=10000)
still_missing = [entry for entry in after if entry.value.get("seen_at") is None]
print(f"AFTER: {len(after)} total seen_items entries")
print(f"Entries still missing seen_at: {len(still_missing)}")
