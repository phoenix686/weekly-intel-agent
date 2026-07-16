"""
Real-embedding live verification for discovery/taste_vectors.py -- covers
BOTH recompute_topic_vectors (topic-vector-recompute) and taste_prefilter
(taste-similarity-prefilter) against the live Supabase Postgres store,
using the local sentence-transformers model. Real topic vectors are
computed from score.py's real TASTE_PROFILE bullets, then a genuinely
on-topic item and a genuinely off-topic item are compared against them.
Deletes every real topic-vector entry it wrote before exiting (even on
assertion failure would leave them -- accepted, matches this project's
existing roundtrip-script pattern of cleanup-after-assert).

Run: uv run --env-file .env python scripts/test_taste_prefilter_live_roundtrip.py
"""
from dotenv import load_dotenv
load_dotenv()

from logging_config import setup_logging
setup_logging()

from discovery.taste_vectors import (
    recompute_topic_vectors, taste_prefilter, TOPIC_TAGS, _TAG_TO_BULLET,
    _NAMESPACE as _VECTORS_NAMESPACE, _DROPS_NAMESPACE,
)
from sunday.memory_store_config import get_store

RUN_ID = "smoke-test-taste-prefilter-live"

PROFILE_TEXT = """\
version: 1
proposal_filters:
  - pattern: "second brain / personal knowledge management agents"
    reason: "rejected - out of scope for weekly intelligence focus"
notes: "User prefers focused intelligence delivery."
"""


def _item(url, title, text):
    return {"url": url, "title": title, "text": text, "author_name": "", "author_handle": "",
            "fetched_at": "2026-07-16T00:00:00+00:00", "is_thread": False, "thread_contents": None,
            "expanded_urls": [], "source": "blog_scrape", "duplicate_count": 1}


print("Step 1: recompute_topic_vectors() with real local embeddings, real TASTE_PROFILE bullets...")
costs = recompute_topic_vectors(PROFILE_TEXT)
mapped_tags = [t for t in TOPIC_TAGS if _TAG_TO_BULLET.get(t) is not None]
print(f"costs: {len(costs)} record(s), {len([c for c in costs if not c.get('error')])} succeeded")

store = get_store()
written = {obj.key: obj.value for obj in store.search(_VECTORS_NAMESPACE, limit=20) if obj.key in mapped_tags}
assert len(written) == len(mapped_tags), f"expected {len(mapped_tags)} real topic vectors, got {len(written)}"
for tag, value in written.items():
    assert len(value["embedding_vector"]) == 384, f"expected 384-dim vector for {tag}, got {len(value['embedding_vector'])}"
print(f"real topic vectors written for: {sorted(written.keys())} (all 384-dim)")

unmapped = [c for c in costs if c.get("error") and "learning-resource" in c["error"]]
assert len(unmapped) == 1
print(f"learning-resource correctly flagged (no vector): {unmapped[0]['error']}")

print("\nStep 2: taste_prefilter() against a genuinely on-topic item and a genuinely off-topic item...")
on_topic = _item(
    "https://example.com/smoke-taste-on-topic",
    "Building LangGraph agent harnesses",
    "A technical walkthrough of agentic engineering patterns, LangGraph agent loops, and tool-dispatch harness design.",
)
off_topic = _item(
    "https://example.com/smoke-taste-off-topic",
    "Best hiking trails in the Pacific Northwest",
    "A travel guide covering scenic hiking trails, trailhead parking, and seasonal weather advice for outdoor enthusiasts.",
)

survivors, taste_costs = taste_prefilter([on_topic, off_topic], run_id=RUN_ID)
print(f"survivors: {[s['url'] for s in survivors]}")
print(f"costs: {taste_costs}")

survivor_urls = {s["url"] for s in survivors}
assert "https://example.com/smoke-taste-on-topic" in survivor_urls, "on-topic item should survive the pre-filter"
assert "https://example.com/smoke-taste-off-topic" not in survivor_urls, "off-topic item should be dropped"

drops = [obj.value for obj in store.search(_DROPS_NAMESPACE, limit=200) if obj.value.get("run_id") == RUN_ID]
assert len(drops) == 1
assert drops[0]["filter_type"] == "taste"
assert drops[0]["compared_against_tag"] is not None
assert drops[0]["compared_against_item_id"] is None
print(f"real prefilter_drops entry: {drops[0]}")

# Cleanup
for tag in mapped_tags:
    store.delete(_VECTORS_NAMESPACE, tag)
for obj in list(store.search(_DROPS_NAMESPACE, limit=500)):
    if obj.value.get("run_id") == RUN_ID:
        store.delete(_DROPS_NAMESPACE, obj.key)

print("\ntaste_prefilter + recompute_topic_vectors live round-trip: PASS")
print(f"  real local embeddings computed for {len(mapped_tags)}/{len(TOPIC_TAGS)} tags (learning-resource correctly flagged, not guessed)")
print("  a genuinely on-topic item survived; a genuinely off-topic item was correctly dropped")
print("  real prefilter_drops audit entry written and read back with correct two-field schema")
print("  all test entries deleted after assertion")
