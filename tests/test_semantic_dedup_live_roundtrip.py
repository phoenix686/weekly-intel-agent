"""
Real-embedding live verification for discovery/semantic_dedup.py (no
mocks) against the live Supabase Postgres store, using the local
sentence-transformers model -- no external account/key needed. Writes
real data under throwaway URLs, verifies, then deletes everything it
wrote (both recent_item_embeddings and prefilter_drops entries).

Run: uv run --env-file .env python scripts/test_semantic_dedup_live_roundtrip.py
"""
from dotenv import load_dotenv
load_dotenv()

from core.logging_config import setup_logging
setup_logging()

from discovery.semantic_dedup import dedupe_semantic, _NAMESPACE as _EMB_NAMESPACE, _DROPS_NAMESPACE
from sunday.memory_store_config import get_store

RUN_ID = "smoke-test-semantic-dedup-live"

def _item(url, title, text, fetched_at):
    return {"url": url, "title": title, "text": text, "author_name": "", "author_handle": "",
            "fetched_at": fetched_at, "is_thread": False, "thread_contents": None,
            "expanded_urls": [], "source": "blog_scrape", "duplicate_count": 1}


earlier = _item(
    "https://example.com/smoke-dedup-earlier",
    "LangGraph agent harness patterns",
    "A deep dive into building agentic engineering harnesses with LangGraph, covering agent loops and tool dispatch.",
    "2026-07-16T08:00:00+00:00",
)
later_duplicate = _item(
    "https://example.com/smoke-dedup-later-duplicate",
    "LangGraph agent harness patterns (re-covered)",
    "A deep dive into building agentic engineering harnesses with LangGraph, covering agent loops and tool dispatch.",
    "2026-07-16T14:00:00+00:00",
)
unrelated = _item(
    "https://example.com/smoke-dedup-unrelated",
    "Chocolate chip cookie recipe",
    "A classic recipe for baking chocolate chip cookies at home, with tips on chilling the dough.",
    "2026-07-16T09:00:00+00:00",
)

print("Running dedupe_semantic() with real local embeddings against 2 near-duplicates + 1 unrelated item...")
survivors, costs = dedupe_semantic([earlier, later_duplicate, unrelated], run_id=RUN_ID)

print(f"survivors: {[s['url'] for s in survivors]}")
print(f"costs: {costs}")

assert len(survivors) == 2, f"expected earlier+unrelated to survive (duplicate dropped), got {len(survivors)}"
survivor_urls = {s["url"] for s in survivors}
assert "https://example.com/smoke-dedup-earlier" in survivor_urls
assert "https://example.com/smoke-dedup-unrelated" in survivor_urls
assert "https://example.com/smoke-dedup-later-duplicate" not in survivor_urls

drop_costs = [c for c in costs if c.get("error") and "duplicate" in c["error"]]
assert len(drop_costs) == 1, f"expected exactly 1 duplicate-drop cost record, got {len(drop_costs)}"
print(f"real drop reasoning: {drop_costs[0]['error']}")

store = get_store()
drops = [obj.value for obj in store.search(_DROPS_NAMESPACE, limit=200) if obj.value.get("run_id") == RUN_ID]
assert len(drops) == 1, f"expected 1 real prefilter_drops entry, got {len(drops)}"
assert drops[0]["filter_type"] == "dedup"
assert drops[0]["compared_against_item_id"] is not None
assert drops[0]["compared_against_tag"] is None
print(f"real prefilter_drops entry: {drops[0]}")

# Cleanup: delete everything this run wrote.
for obj in list(store.search(_EMB_NAMESPACE, limit=500)):
    if obj.key.startswith("https://example.com/smoke-dedup-"):
        store.delete(_EMB_NAMESPACE, obj.key)
for obj in list(store.search(_DROPS_NAMESPACE, limit=500)):
    if obj.value.get("run_id") == RUN_ID:
        store.delete(_DROPS_NAMESPACE, obj.key)

print("\nsemantic_dedup live round-trip: PASS")
print("  real local embeddings correctly collapsed a genuine near-duplicate pair (earlier-published kept)")
print("  a genuinely unrelated item correctly survived (not falsely flagged as a duplicate)")
print("  real prefilter_drops audit entry written and read back with correct two-field schema")
print("  all test entries deleted after assertion")
