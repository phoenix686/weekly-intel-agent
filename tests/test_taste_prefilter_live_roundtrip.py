"""
Real-embedding live verification for discovery/taste_vectors.py -- covers
BOTH recompute_topic_vectors (topic-vector-recompute) and taste_prefilter
(taste-similarity-prefilter) against the live Supabase Postgres store,
via the real NVIDIA NIM embedding provider (discovery/embeddings.py --
corrected 2026-07-22; this previously asserted the OLD local
sentence-transformers model's 384-dim vectors, stale since the
2026-07-19 NVIDIA swap to 2048-dim, meaning nobody had actually run this
script successfully since that swap). Real topic vectors are computed
from score.py's real TASTE_PROFILE bullets, then a genuinely on-topic
item and a genuinely off-topic item are compared against them.

*** DANGER, READ BEFORE RUNNING AGAINST A REAL DB_URI ***
Step 1 calls recompute_topic_vectors() with this file's own fake
PROFILE_TEXT (below), which OVERWRITES the real production topic
vectors in ("weekly_intel","taste_topic_vectors") -- same keys, no
run_id scoping on that namespace. Cleanup only DELETES what it wrote; it
does NOT restore whatever real vectors existed before this script ran.
A real production DB_URI's real topic vectors will be left EMPTY after
a clean run, and left corrupted (fake-profile-derived) after any
failure that skips cleanup -- confirmed the hard way, 2026-07-22: the
stale 384-dim assertion above crashed this script mid-run against the
real production store, leaving all 6 real topic vectors overwritten
with vectors computed from this file's fake PROFILE_TEXT until manually
restored via a fresh recompute_topic_vectors(real data/taste_profile.yaml
content). This script's cleanup-only-deletes design needs a real
save-and-restore fix (or should stop targeting the same DB_URI as
production) before it's safe to run again without manual recovery on
either a crash OR a normal successful pass -- flagged here rather than
silently patched around, since it's a design decision, not a one-line fix.

Run: uv run --env-file .env python tests/test_taste_prefilter_live_roundtrip.py
"""
from dotenv import load_dotenv
load_dotenv()

from core.logging_config import setup_logging
setup_logging()

from discovery.taste_vectors import (
    recompute_topic_vectors, taste_prefilter, TOPIC_TAGS, _TAG_TO_BULLET,
    _NAMESPACE as _VECTORS_NAMESPACE, _DROPS_NAMESPACE,
)
from discovery.embeddings import EMBEDDING_DIM
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
    assert len(value["embedding_vector"]) == EMBEDDING_DIM, f"expected {EMBEDDING_DIM}-dim vector for {tag}, got {len(value['embedding_vector'])}"
print(f"real topic vectors written for: {sorted(written.keys())} (all {EMBEDDING_DIM}-dim)")

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

survivors, uncategorized, taste_costs = taste_prefilter([on_topic, off_topic], run_id=RUN_ID)
print(f"survivors: {[s['url'] for s in survivors]}")
print(f"uncategorized: {[(u['url'], u['best_tag'], u['similarity_score']) for u in uncategorized]}")
print(f"costs: {taste_costs}")

survivor_urls = {s["url"] for s in survivors}
assert "https://example.com/smoke-taste-on-topic" in survivor_urls, "on-topic item should survive the pre-filter"
assert "https://example.com/smoke-taste-off-topic" not in survivor_urls, "off-topic item should be dropped from survivors"

# Real evidence for lightweight-uncategorized-flagging (2026-07-22): the
# off-topic item is no longer just silently dropped -- it must come back
# as a real uncategorized entry, with a real best_tag/similarity_score
# from the actual live embeddings, not a placeholder.
assert len(uncategorized) == 1, f"expected exactly 1 uncategorized item, got {len(uncategorized)}"
assert uncategorized[0]["url"] == "https://example.com/smoke-taste-off-topic"
assert uncategorized[0]["best_tag"] in mapped_tags
assert isinstance(uncategorized[0]["similarity_score"], float)
assert uncategorized[0]["similarity_score"] < 0.30
print(f"real uncategorized entry: best_tag={uncategorized[0]['best_tag']!r}, similarity_score={uncategorized[0]['similarity_score']:.3f}")

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
print("  a genuinely on-topic item survived; a genuinely off-topic item was correctly dropped from survivors")
print("  the off-topic item was ALSO returned as a real uncategorized entry (best_tag/similarity_score), not silently lost")
print("  real prefilter_drops audit entry written and read back with correct two-field schema")
print("  all test entries deleted after assertion")
