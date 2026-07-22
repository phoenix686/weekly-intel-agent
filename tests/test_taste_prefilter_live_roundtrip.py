"""
Real-embedding live verification for discovery/taste_vectors.py -- covers
BOTH recompute_topic_vectors (topic-vector-recompute) and taste_prefilter
(taste-similarity-prefilter) against a REAL Postgres store, via the real
NVIDIA NIM embedding provider (discovery/embeddings.py).

ISOLATION (2026-07-22, fixed after a real incident): this script used to
call get_store() directly -- the SAME connection pool/schema as
production (core/connection_pool.py's DB_URI is the one and only
connection string this whole project uses; there is no separate test/
staging database anywhere). Step 1 overwrites the real topic-vector rows
(("weekly_intel","taste_topic_vectors"), no run_id scoping on that
namespace) with vectors computed from this file's own fake PROFILE_TEXT.
The old "cleanup" only deleted what it wrote -- it never restored prior
state, so even a clean pass left production's real topic vectors EMPTY
afterward, and a crash left them corrupted (fake-profile-derived)
indefinitely. Confirmed the hard way: a stale 384-dim assertion (left
over from the pre-2026-07-19 local-embedding provider, since replaced by
a 2048-dim NVIDIA model) crashed this script mid-run against the real
production store, and the fake vectors sat there until manually restored
from the real data/taste_profile.yaml.

Fix: every store operation in this script now goes through a dedicated
Postgres SCHEMA (_TEST_SCHEMA below) on the SAME Supabase instance/
DB_URI -- no new credential or secret needed. discovery.taste_vectors's
module-level get_store() is patched for the duration of this script to
return a store bound to that schema. langgraph.store.postgres.base's
migrations create unqualified table names (`store`, `store_vectors`,
`store_migrations`) resolved via the connection's search_path, so
binding to a schema that ONLY contains this script's own tables makes it
structurally impossible for this script -- crash or not -- to read,
write, or delete a single row of the real production
("weekly_intel","taste_topic_vectors") or any other real namespace. The
schema is dropped and recreated at the start of every run for a
guaranteed-clean slate, and dropped again at the end in a finally block
-- "cleanup" is now just tidiness, not a safety mechanism, since
production was never reachable in the first place.

GUARD: refuses to run at all unless ALLOW_LIVE_TASTE_VECTOR_TEST=1 is
set -- this script makes real, costed NVIDIA embedding API calls and
should never fire without deliberate intent, regardless of the
isolation fix above.

Run: ALLOW_LIVE_TASTE_VECTOR_TEST=1 uv run --env-file .env python tests/test_taste_prefilter_live_roundtrip.py
"""
import os
import sys

if os.environ.get("ALLOW_LIVE_TASTE_VECTOR_TEST") != "1":
    print(
        "Refusing to run: this script makes real, costed NVIDIA embedding "
        "API calls and touches a real (isolated-schema, but still real) "
        "Postgres connection. Set ALLOW_LIVE_TASTE_VECTOR_TEST=1 to run it "
        "deliberately:\n"
        "  ALLOW_LIVE_TASTE_VECTOR_TEST=1 uv run --env-file .env python "
        "tests/test_taste_prefilter_live_roundtrip.py"
    )
    sys.exit(1)

import psycopg
from unittest.mock import patch
from langgraph.store.postgres import PostgresStore

from dotenv import load_dotenv
load_dotenv()

from core.logging_config import setup_logging
setup_logging()

from discovery.taste_vectors import (
    recompute_topic_vectors, taste_prefilter, TOPIC_TAGS, _TAG_TO_BULLET,
    _NAMESPACE as _VECTORS_NAMESPACE, _DROPS_NAMESPACE,
)
from discovery.embeddings import EMBEDDING_DIM

RUN_ID = "smoke-test-taste-prefilter-live"

# Dedicated schema, same Supabase instance/DB_URI, zero overlap with the
# real "public" schema production actually uses -- see module docstring.
_TEST_SCHEMA = "test_taste_vectors_live"

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


def _drop_test_schema(conn) -> None:
    conn.execute(f"DROP SCHEMA IF EXISTS {_TEST_SCHEMA} CASCADE")


def _make_isolated_store():
    """A real PostgresStore, but bound via search_path to a schema this
    script owns exclusively -- never the real production schema, no
    matter how this script exits. Dropped+recreated here for a clean
    slate; dropped again in the caller's finally block."""
    setup_conn = psycopg.connect(os.environ["DB_URI"], autocommit=True)
    _drop_test_schema(setup_conn)
    setup_conn.execute(f"CREATE SCHEMA {_TEST_SCHEMA}")
    setup_conn.close()

    conn = psycopg.connect(
        os.environ["DB_URI"], autocommit=True,
        options=f"-c search_path={_TEST_SCHEMA},public",
    )
    store = PostgresStore(conn=conn)
    store.setup()
    return store, conn


def main() -> None:
    isolated_store, isolated_conn = _make_isolated_store()
    try:
        with patch("discovery.taste_vectors.get_store", return_value=isolated_store):
            print(f"[isolated schema: {_TEST_SCHEMA}] Step 1: recompute_topic_vectors() with real embeddings, real TASTE_PROFILE bullets...")
            costs = recompute_topic_vectors(PROFILE_TEXT)
            mapped_tags = [t for t in TOPIC_TAGS if _TAG_TO_BULLET.get(t) is not None]
            print(f"costs: {len(costs)} record(s), {len([c for c in costs if not c.get('error')])} succeeded")

            written = {obj.key: obj.value for obj in isolated_store.search(_VECTORS_NAMESPACE, limit=20) if obj.key in mapped_tags}
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

            drops = [obj.value for obj in isolated_store.search(_DROPS_NAMESPACE, limit=200) if obj.value.get("run_id") == RUN_ID]
            assert len(drops) == 1
            assert drops[0]["filter_type"] == "taste"
            assert drops[0]["compared_against_tag"] is not None
            assert drops[0]["compared_against_item_id"] is None
            print(f"real prefilter_drops entry: {drops[0]}")

        print("\ntaste_prefilter + recompute_topic_vectors live round-trip: PASS")
        print(f"  real embeddings computed for {len(mapped_tags)}/{len(TOPIC_TAGS)} tags (learning-resource correctly flagged, not guessed)")
        print("  a genuinely on-topic item survived; a genuinely off-topic item was correctly dropped from survivors")
        print("  the off-topic item was ALSO returned as a real uncategorized entry (best_tag/similarity_score), not silently lost")
        print("  real prefilter_drops audit entry written and read back with correct two-field schema")
        print(f"  everything above happened inside schema {_TEST_SCHEMA!r} -- production's real topic vectors were never touched")
    finally:
        # Tidiness only, not safety -- see module docstring. Runs even on
        # assertion failure or any other exception raised above.
        isolated_conn.execute(f"DROP SCHEMA IF EXISTS {_TEST_SCHEMA} CASCADE")
        isolated_conn.close()


if __name__ == "__main__":
    main()
