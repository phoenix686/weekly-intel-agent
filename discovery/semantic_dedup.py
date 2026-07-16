"""
Cross-source/cross-run semantic dedup (batch2-dedup-taste-spec.md Section
4): catches same-story-different-URL duplicates that URL-heuristic dedup
(discovery/nodes/cluster_dedupe.py's _dedupe) and seen_items (both
URL-keyed) structurally cannot -- different URL, same underlying story.

Embeds each surviving item's title+text (gemini-embedding-001 via Google
AI Studio, discovery/embeddings.py), compares cosine similarity against a
rolling 7-day window of previously-processed item embeddings stored under
namespace=("weekly_intel","recent_item_embeddings"). Threshold 0.90 --
looser than a general-news dedup API's 0.95, since this project's AI/tech
content has naturally higher baseline topical overlap.

Two distinct comparisons -- this split is an implementation detail beyond
the spec's literal text (which describes comparing "the rolling window"
as one mechanism), kept deliberately:
  - Cross-run (against the persisted window): a match means an equivalent
    story already went out in an earlier, already-completed run's
    digest/plan -- the new item is dropped unconditionally. There's
    nothing to retroactively "swap": the window entry was already sent.
  - Within-run (against items already kept earlier in this same call):
    a match means two sources covered the same story in the same batch,
    and NEITHER has been sent anywhere yet -- keep whichever was
    published earlier (fetched_at), not whichever has fuller text, per
    the spec's tie-breaker (verbosity isn't quality; the earlier item is
    more likely the original reporting, a later one more likely a
    derivative summary).

Every drop (either kind) is logged to ("weekly_intel","prefilter_drops")
per Section 8's two-field audit schema, in addition to NodeCost.error.

Ad-hoc items never reach this function at all -- cluster_dedupe_node
splits them out before calling in, per Section 10.

A failed embed call degrades gracefully: the item passes through
untouched (not compared, not dropped, not persisted to the window).

No langgraph imports.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from discovery.embeddings import embed_text, cosine_similarity, COST_PER_TOKEN_USD
from sunday.memory_store_config import get_store
from state import ClusteredItem, NodeCost

logger = logging.getLogger(__name__)

_NAMESPACE = ("weekly_intel", "recent_item_embeddings")
_DROPS_NAMESPACE = ("weekly_intel", "prefilter_drops")
_WINDOW_DAYS = 7
_THRESHOLD = 0.90


def _load_window() -> list[dict]:
    """Every non-expired entry in the rolling window. Lazily deletes any
    entry older than _WINDOW_DAYS as it's encountered -- keeps the
    namespace bounded with no separate cleanup job."""
    store = get_store()
    cutoff = datetime.now(timezone.utc) - timedelta(days=_WINDOW_DAYS)
    live: list[dict] = []
    for item_obj in store.search(_NAMESPACE, limit=1000):
        value = item_obj.value
        scored_at = datetime.fromisoformat(value["scored_at"])
        if scored_at < cutoff:
            store.delete(_NAMESPACE, item_obj.key)
            continue
        live.append(value)
    return live


def _log_drop(store, item_id: str, similarity: float, compared_against_item_id: str, run_id: str) -> None:
    store.put(_DROPS_NAMESPACE, str(uuid.uuid4()), {
        "item_id": item_id,
        "filter_type": "dedup",
        "similarity_score": similarity,
        "compared_against_item_id": compared_against_item_id,
        "compared_against_tag": None,
        "run_id": run_id,
    })


def dedupe_semantic(items: list[ClusteredItem], run_id: str = "unknown") -> tuple[list[ClusteredItem], list[NodeCost]]:
    """Returns (surviving_items, cost_records)."""
    store = get_store()
    window = _load_window()
    survivors: list[ClusteredItem] = []
    survivor_vectors: list[list[float] | None] = []
    costs: list[NodeCost] = []

    for item in items:
        try:
            vector, tokens = embed_text(f"{item['title']}\n\n{item['text']}")
        except Exception as e:
            logger.warning(f"semantic_dedup: embed failed for {item['url']!r}, passing through unfiltered: {e}")
            survivors.append(item)
            survivor_vectors.append(None)
            costs.append(NodeCost(
                node_name="semantic_dedup", input_tokens=0, output_tokens=0,
                cost_usd=0.0, latency_ms=0.0,
                error=f"embed failed, item passed through unfiltered: {e}",
            ))
            continue

        cost_usd = round(tokens * COST_PER_TOKEN_USD, 8)

        cross_run_match = next(
            ((entry, cosine_similarity(vector, entry["embedding_vector"])) for entry in window
             if cosine_similarity(vector, entry["embedding_vector"]) >= _THRESHOLD),
            None,
        )
        if cross_run_match is not None:
            entry, sim = cross_run_match
            _log_drop(store, item["url"], sim, entry["url"], run_id)
            costs.append(NodeCost(
                node_name="semantic_dedup", input_tokens=tokens, output_tokens=0,
                cost_usd=cost_usd, latency_ms=0.0,
                error=f"dropped as duplicate of previously-seen {entry['url']} (cosine={sim:.3f})",
            ))
            continue

        within_run_match = next(
            ((idx, cosine_similarity(vector, sv)) for idx, sv in enumerate(survivor_vectors)
             if sv is not None and cosine_similarity(vector, sv) >= _THRESHOLD),
            None,
        )
        if within_run_match is None:
            survivors.append(item)
            survivor_vectors.append(vector)
            costs.append(NodeCost(
                node_name="semantic_dedup", input_tokens=tokens, output_tokens=0,
                cost_usd=cost_usd, latency_ms=0.0,
            ))
        else:
            idx, sim = within_run_match
            existing = survivors[idx]
            # Earliest-published wins -- not fuller text (verbosity isn't
            # quality). Ties (identical fetched_at) keep the existing
            # survivor, matching _pick_representative's tie-break style
            # elsewhere in the pipeline.
            if item["fetched_at"] < existing["fetched_at"]:
                dropped_url = existing["url"]
                survivors[idx] = item
                survivor_vectors[idx] = vector
                _log_drop(store, dropped_url, sim, item["url"], run_id)
                costs.append(NodeCost(
                    node_name="semantic_dedup", input_tokens=tokens, output_tokens=0,
                    cost_usd=cost_usd, latency_ms=0.0,
                    error=f"kept over near-duplicate {dropped_url} (cosine={sim:.3f}, published earlier)",
                ))
            else:
                _log_drop(store, item["url"], sim, existing["url"], run_id)
                costs.append(NodeCost(
                    node_name="semantic_dedup", input_tokens=tokens, output_tokens=0,
                    cost_usd=cost_usd, latency_ms=0.0,
                    error=f"dropped as duplicate of {existing['url']} (cosine={sim:.3f})",
                ))

    for item, vector in zip(survivors, survivor_vectors):
        if vector is None:
            continue
        store.put(_NAMESPACE, item["url"], {
            "item_id": item["url"],
            "url": item["url"],
            "embedding_vector": vector,
            "fetched_at": item["fetched_at"],
            "scored_at": datetime.now(timezone.utc).isoformat(),
        })

    return survivors, costs
