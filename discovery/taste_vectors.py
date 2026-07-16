"""
Multi-vector per-tag taste-similarity pre-filter (batch2-dedup-taste-spec.md
Section 4): one embedding per real topic tag (score.py's ALLOWED_TAGS minus
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

Topic vectors are recomputed by recompute_topic_vectors(), called from
sunday/approval_actions.py immediately after the taste-profile YAML
regenerates (see that file for why -- not sunday/nodes/update_profile.py,
despite the spec text's literal wording; see WORKFLOW.md).

No langgraph imports.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from discovery.embeddings import embed_text, cosine_similarity, COST_PER_TOKEN_USD
from discovery.nodes.score import ALLOWED_TAGS
from sunday.memory_store_config import get_store
from state import ClusteredItem, NodeCost

logger = logging.getLogger(__name__)

_NAMESPACE = ("weekly_intel", "taste_topic_vectors")
_THRESHOLD = 0.30

# Real topics only -- "noise" is score_node's catch-all drop tag, not a
# taste facet to build a comparison vector for.
TOPIC_TAGS = sorted(ALLOWED_TAGS - {"noise"})


def recompute_topic_vectors(profile_text: str) -> list[NodeCost]:
    """One embedding per TOPIC_TAGS entry, each anchored by its tag name
    plus the current full taste-profile text -- keeps every vector
    distinct (different leading tag string) while staying in sync with
    whatever the profile currently says (re-embeds the live text every
    call, same call as the YAML regen it follows)."""
    store = get_store()
    costs: list[NodeCost] = []
    now = datetime.now(timezone.utc).isoformat()

    for tag in TOPIC_TAGS:
        try:
            vector, tokens = embed_text(f"{tag}\n\n{profile_text}")
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

    logger.info(f"taste_vectors: recomputed {len([c for c in costs if not c.get('error')])}/{len(TOPIC_TAGS)} topic vectors")
    return costs


def _load_topic_vectors() -> list[dict]:
    store = get_store()
    return [item_obj.value for item_obj in store.search(_NAMESPACE, limit=len(TOPIC_TAGS) + 5)]


def taste_prefilter(items: list[ClusteredItem]) -> tuple[list[ClusteredItem], list[NodeCost]]:
    """Returns (surviving_items, cost_records)."""
    topic_vectors = _load_topic_vectors()
    if not topic_vectors:
        # No vectors computed yet (e.g. before the first real feedback
        # event) -- permissive default: let everything through rather
        # than dropping everything against an empty comparison set.
        logger.info("taste_vectors: no topic vectors yet, pre-filter skipped for this run")
        return items, []

    survivors: list[ClusteredItem] = []
    costs: list[NodeCost] = []

    for item in items:
        try:
            vector, tokens = embed_text(f"{item['title']}\n\n{item['text']}")
        except Exception as e:
            logger.warning(f"taste_vectors: embed failed for {item['url']!r}, passing through unfiltered: {e}")
            survivors.append(item)
            costs.append(NodeCost(
                node_name="taste_prefilter", input_tokens=0, output_tokens=0,
                cost_usd=0.0, latency_ms=0.0,
                error=f"embed failed, item passed through unfiltered: {e}",
            ))
            continue

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
            costs.append(NodeCost(
                node_name="taste_prefilter", input_tokens=tokens, output_tokens=0,
                cost_usd=cost_usd, latency_ms=0.0,
                error=f"dropped by taste pre-filter: best match {best_tag!r} cosine={best_sim:.3f} < {_THRESHOLD}",
            ))

    return survivors, costs
