"""
Multi-vector per-tag taste-similarity pre-filter (batch2-dedup-taste-spec.md
Section 5): one embedding per real topic tag (score.py's ALLOWED_TAGS minus
"noise", which is a drop bucket, not a topic), instead of a single vector
for the whole taste profile -- averaging everything into one vector would
blur distinct interests together.

Compares each item against every topic vector, takes the MAX similarity
(not average) -- an item that's a strong match for one topic and
irrelevant to the rest should score on its best match. Threshold 0.30,
deliberately permissive: this only cuts near-zero-relevance items before
score_node's paid Haiku call, never replaces it. A false negative here
(filtering out something genuinely good) is worse than a false positive
(something mediocre reaches score_node and gets scored "drop" there, at
trivial extra cost) -- so failures and edge cases always favor letting
items through.

Every drop is logged to ("weekly_intel","prefilter_drops") per Section
8's two-field audit schema, in addition to NodeCost.error.

Ad-hoc items never reach this function at all -- cluster_dedupe_node
splits them out before calling in, per Section 10.

Topic vectors are recomputed by recompute_topic_vectors(), called from
sunday/nodes/update_profile.py's Sunday consolidated rewrite, immediately
after the fresh taste_profile.yaml is written (Section 7) -- NOT
sunday/approval_actions.py, which was the call site under this
checkpoint's earlier (superseded) design; approval_actions.py now only
logs feedback and stops (item-feedback-logging).

Bootstrap/embedding input per tag is score.py's TASTE_PROFILE prompt
constant, mapped best-effort to the 6 fixed tags (Section 0 item 1,
Section 6) -- 'learning-resource' has no clearly corresponding bullet in
TASTE_PROFILE and is flagged rather than guessed: no vector is computed
for it until a real mapping is confirmed.

No langgraph imports.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from langgraph.store.base import PutOp

from discovery.embeddings import embed_text, embed_texts, cosine_similarity, COST_PER_TOKEN_USD
from discovery.nodes.score import ALLOWED_TAGS
from sunday.memory_store_config import get_store
from state import ClusteredItem, NodeCost

logger = logging.getLogger(__name__)

_NAMESPACE = ("weekly_intel", "taste_topic_vectors")
_DROPS_NAMESPACE = ("weekly_intel", "prefilter_drops")
_THRESHOLD = 0.30

# Real topics only -- "noise" is score_node's catch-all drop tag, not a
# taste facet to build a comparison vector for.
TOPIC_TAGS = sorted(ALLOWED_TAGS - {"noise"})

# Best-effort mapping from each tag to its corresponding TASTE_PROFILE
# bullet (discovery/nodes/score.py) -- the real per-tag "description"
# text this project has, per Section 0 item 1's investigation (the YAML
# taste_profile.yaml has no per-tag text at all). 'learning-resource' has
# no bullet that clearly corresponds to it (the closest candidate, "AI
# engineering as a role", is about career/interview content, not learning
# format) -- mapped to None deliberately, flagged in recompute rather
# than guessed.
_TAG_TO_BULLET = {
    "agentic-engineering": "Agentic frameworks and patterns (LangGraph, LangChain, agent loops, harness engineering)",
    "memory-systems": "Memory systems for AI agents (LangMem, Mem0, vector stores, knowledge graphs)",
    "llm-tooling": "LLM tooling, APIs, SDKs, prompt engineering, context engineering",
    "evals": "Evals, observability, tracing, LangSmith",
    "distributed-systems": "Distributed systems and infrastructure applicable to AI agents",
    "learning-resource": None,
}


def recompute_topic_vectors(profile_text: str) -> list[NodeCost]:
    """One embedding per TOPIC_TAGS entry that has a mapped TASTE_PROFILE
    bullet, anchored by that bullet plus the current full taste-profile
    text -- keeps every vector distinct (different leading bullet) while
    staying in sync with whatever the profile currently says (re-embeds
    the live text every call, same call as the Sunday YAML regen it
    follows). Tags with no mapped bullet are flagged, not embedded from
    guessed text -- no vector is written for them."""
    store = get_store()
    costs: list[NodeCost] = []
    now = datetime.now(timezone.utc).isoformat()

    for tag in TOPIC_TAGS:
        bullet = _TAG_TO_BULLET.get(tag)
        if bullet is None:
            logger.warning(f"taste_vectors: no clearly corresponding TASTE_PROFILE bullet for tag {tag!r} -- flagged, not guessed, no vector computed")
            costs.append(NodeCost(
                node_name="recompute_topic_vectors", input_tokens=0, output_tokens=0,
                cost_usd=0.0, latency_ms=0.0,
                error=f"no clearly corresponding TASTE_PROFILE bullet for tag {tag!r} -- flagged rather than guessed, no vector computed",
            ))
            continue

        try:
            vector, tokens = embed_text(f"{bullet}\n\n{profile_text}")
        except Exception as e:
            logger.warning(f"taste_vectors: recompute failed for tag {tag!r}: {e}")
            costs.append(NodeCost(
                node_name="recompute_topic_vectors", input_tokens=0, output_tokens=0,
                cost_usd=0.0, latency_ms=0.0, error=f"recompute failed for tag {tag!r}: {e}",
            ))
            continue

        store.put(_NAMESPACE, tag, {"tag": tag, "embedding_vector": vector, "updated_at": now})
        costs.append(NodeCost(
            node_name="recompute_topic_vectors", input_tokens=tokens, output_tokens=0,
            cost_usd=round(tokens * COST_PER_TOKEN_USD, 8), latency_ms=0.0,
        ))

    mapped_count = sum(1 for tag in TOPIC_TAGS if _TAG_TO_BULLET.get(tag) is not None)
    logger.info(f"taste_vectors: recomputed {len([c for c in costs if not c.get('error')])}/{mapped_count} mapped topic vectors ({len(TOPIC_TAGS) - mapped_count} tag(s) unmapped, flagged)")
    return costs


def _load_topic_vectors() -> list[dict]:
    store = get_store()
    return [item_obj.value for item_obj in store.search(_NAMESPACE, limit=len(TOPIC_TAGS) + 5)]


def _drop_record(item_id: str, similarity: float, compared_against_tag: str, run_id: str) -> dict:
    """Builds a prefilter_drops record without writing it -- collected
    across the per-item loop and written in ONE store.batch() call at the
    end, instead of one store.put() per drop (same batching fix as
    filter_unseen/mark_seen and semantic_dedup.py, 2026-07-17)."""
    return {
        "item_id": item_id,
        "filter_type": "taste",
        "similarity_score": similarity,
        "compared_against_item_id": None,
        "compared_against_tag": compared_against_tag,
        "run_id": run_id,
    }


def taste_prefilter(items: list[ClusteredItem], run_id: str = "unknown") -> tuple[list[ClusteredItem], list[NodeCost]]:
    """Returns (surviving_items, cost_records)."""
    store = get_store()
    topic_vectors = _load_topic_vectors()
    if not topic_vectors:
        # No vectors computed yet (e.g. before the first real feedback
        # event) -- permissive default: let everything through rather
        # than dropping everything against an empty comparison set.
        logger.info("taste_vectors: no topic vectors yet, pre-filter skipped for this run")
        return items, []

    survivors: list[ClusteredItem] = []
    costs: list[NodeCost] = []

    if not items:
        return survivors, costs

    # Embed every item in ONE batched call -- see semantic_dedup.py's
    # dedupe_semantic() for the same fix and the measured ~3.4x speedup
    # (150-item local benchmark: 3.05s looped vs 0.89s batched). A
    # batch-wide failure now degrades every item at once rather than
    # per-item -- acceptable for a local model, where a failure here
    # realistically means the model itself is broken.
    try:
        all_vectors, all_tokens = embed_texts([f"{item['title']}\n\n{item['text']}" for item in items])
    except Exception as e:
        logger.warning(f"taste_vectors: batch embed failed, all {len(items)} item(s) passed through unfiltered: {e}")
        return list(items), [
            NodeCost(
                node_name="taste_prefilter", input_tokens=0, output_tokens=0,
                cost_usd=0.0, latency_ms=0.0,
                error=f"embed failed, item passed through unfiltered: {e}",
            )
            for _ in items
        ]

    drop_records: list[dict] = []

    for item, vector, tokens in zip(items, all_vectors, all_tokens):
        best_tag, best_sim = max(
            ((tv["tag"], cosine_similarity(vector, tv["embedding_vector"])) for tv in topic_vectors),
            key=lambda pair: pair[1],
        )
        cost_usd = round(tokens * COST_PER_TOKEN_USD, 8)

        if best_sim >= _THRESHOLD:
            survivors.append(item)
            costs.append(NodeCost(
                node_name="taste_prefilter", input_tokens=tokens, output_tokens=0,
                cost_usd=cost_usd, latency_ms=0.0,
            ))
        else:
            drop_records.append(_drop_record(item["url"], best_sim, best_tag, run_id))
            costs.append(NodeCost(
                node_name="taste_prefilter", input_tokens=tokens, output_tokens=0,
                cost_usd=cost_usd, latency_ms=0.0,
                error=f"dropped by taste pre-filter: best match {best_tag!r} cosine={best_sim:.3f} < {_THRESHOLD}",
            ))

    # One batched call covering every drop, instead of one store.put()
    # per drop -- same fix as filter_unseen/mark_seen/semantic_dedup.py.
    if drop_records:
        store.batch([PutOp(_DROPS_NAMESPACE, str(uuid.uuid4()), record) for record in drop_records])

    return survivors, costs
