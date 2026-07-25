"""
Cross-source/cross-run semantic dedup (batch2-dedup-taste-spec.md Section
4): catches same-story-different-URL duplicates that URL-heuristic dedup
(discovery/nodes/cluster_dedupe.py's _dedupe) and seen_items (both
URL-keyed) structurally cannot -- different URL, same underlying story.

Embeds each surviving item's title+text locally (sentence-transformers,
all-MiniLM-L6-v2, discovery/embeddings.py), compares cosine similarity against a
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
import time
import uuid
from datetime import datetime, timedelta, timezone

from langgraph.store.base import PutOp

from discovery.embeddings import (
    embed_texts, cosine_similarity, COST_PER_TOKEN_USD,
    MAX_EMBED_CHARS as _MAX_EMBED_CHARS, record_embedding_failure,
)
from sunday.memory_store_config import get_store
from core.state import ClusteredItem, NodeCost

logger = logging.getLogger(__name__)

_NAMESPACE = ("weekly_intel", "recent_item_embeddings")
_DROPS_NAMESPACE = ("weekly_intel", "prefilter_drops")
_WINDOW_DAYS = 7
_THRESHOLD = 0.90

# Second, lower tier: catches "same announcement, different dedicated
# article" (e.g. a MarkTechPost writeup and an x.com post about the same
# Cursor Router launch, 10 hours apart across two runs) -- confirmed real,
# NOT caught by _THRESHOLD since the two articles are worded completely
# differently, just about the same story. Calibrated 2026-07-23 against
# the two real confirmed pairs available that session (Laguna S 2.1
# HF-repo vs. MarkTechPost write-up, within-run: cosine 0.6423; the Cursor
# Router MarkTechPost-vs-x.com pair, cross-run: cosine 0.6356) against 4
# real unrelated-item control pairs (highest control: 0.5435). A THIN
# margin off only 2 positive data points -- treat as a starting point to
# refine as more real runs accumulate, not a final number. Never applied
# when either side is a roundup-style item -- see _is_roundup_item.
_CONTENT_OVERLAP_THRESHOLD = 0.60

# MAX_EMBED_CHARS and record_embedding_failure() now live in
# discovery/embeddings.py -- shared with discovery/taste_vectors.py,
# which hit the exact same oversized-batch 400 (2026-07-25) and needed
# the identical fix. See that module for the full rationale.


def _load_window() -> list[dict]:
    """Every non-expired entry in the rolling window. Lazily deletes any
    entry older than _WINDOW_DAYS as it's encountered -- keeps the
    namespace bounded with no separate cleanup job."""
    store = get_store()
    cutoff = datetime.now(timezone.utc) - timedelta(days=_WINDOW_DAYS)
    live: list[dict] = []

    logger.debug("semantic_dedup: BEFORE store.search() (_load_window)")
    t0 = time.perf_counter()
    window_entries = store.search(_NAMESPACE, limit=1000)
    logger.debug(f"semantic_dedup: AFTER store.search() (_load_window) ({time.perf_counter() - t0:.3f}s, {len(window_entries)} entries)")

    for item_obj in window_entries:
        value = item_obj.value
        scored_at = datetime.fromisoformat(value["scored_at"])
        if scored_at < cutoff:
            logger.debug(f"semantic_dedup: BEFORE store.delete() (stale window entry {item_obj.key!r})")
            t0 = time.perf_counter()
            store.delete(_NAMESPACE, item_obj.key)
            logger.debug(f"semantic_dedup: AFTER store.delete() (stale window entry {item_obj.key!r}) ({time.perf_counter() - t0:.3f}s)")
            continue
        live.append(value)
    return live


def _drop_record(item_id: str, similarity: float, compared_against_item_id: str, run_id: str,
                  filter_type: str = "dedup") -> dict:
    """Builds a prefilter_drops record without writing it -- the caller
    collects these across the whole per-item loop and writes them all in
    ONE store.batch() call at the end, instead of one store.put() per
    drop (same batching fix as filter_unseen/mark_seen, 2026-07-17).

    filter_type distinguishes the two dedup tiers ("dedup" = near-verbatim
    @ _THRESHOLD, "content_overlap" = same-story-different-article @
    _CONTENT_OVERLAP_THRESHOLD) so the real similarity_score distribution
    per tier stays queryable here as more runs accumulate -- 0.60 was
    calibrated from exactly 2 confirmed real pairs and 4 controls
    (2026-07-23), a starting point to refine, not a locked number."""
    return {
        "item_id": item_id,
        "filter_type": filter_type,
        "similarity_score": similarity,
        "compared_against_item_id": compared_against_item_id,
        "compared_against_tag": None,
        "run_id": run_id,
    }


def _is_roundup_item(item: dict) -> bool:
    """Identifies aggregator/roundup-style content that the content-overlap
    tier (see _CONTENT_OVERLAP_THRESHOLD) must never drop against, per
    Case A from the 2026-07-23 content-overlap investigation: a roundup's
    single whole-document embedding is a blend across many stories, so a
    high similarity to one dedicated article is not reliable evidence of
    which specific story overlaps (real calibration data showed confirmed
    roundup/individual overlaps scoring anywhere from 0.03 to 0.58,
    fully overlapping the unrelated-control range) -- catching that
    properly needs per-story chunking, deferred to its own design pass.
    This guard only protects the NEW lower threshold; the existing
    near-verbatim _THRESHOLD tier is unaffected (a roundup being
    near-identical to a previous run's near-identical roundup is still a
    real duplicate).

    source == "TLDR AI" matches blog_sources.yaml's roundup: true config
    flag (even though TLDR issues are pre-split into individual blurbs by
    fetch_tldr_roundup() before reaching this function -- kept as a
    defensive match on the literal source name). The "[AINews]" title
    prefix is the real, currently-observed signal (Latent Space's
    aggregation-format posts) -- same prefix-based identification pattern
    already used for Hacker News's "Show HN:" in discovery/nodes/score.py."""
    return item.get("source") == "TLDR AI" or (item.get("title") or "").startswith("[AINews]")


def dedupe_semantic(items: list[ClusteredItem], run_id: str = "unknown") -> tuple[list[ClusteredItem], list[NodeCost]]:
    """Returns (surviving_items, cost_records)."""
    store = get_store()
    window = _load_window()
    survivors: list[ClusteredItem] = []
    survivor_vectors: list[list[float]] = []
    costs: list[NodeCost] = []

    if not items:
        return survivors, costs

    # Embed every item in ONE batched call -- sentence-transformers
    # batches the real forward pass internally, measured ~3.4x faster
    # than calling embed_text() once per item in this loop (150-item
    # local benchmark: 3.05s looped vs 0.89s batched). A batch-wide
    # failure now degrades every item at once rather than per-item --
    # acceptable since this graceful-degradation path was written for an
    # API-based provider (one request can fail while others succeed); for
    # a local model, a failure here realistically means the model itself
    # is broken, which would fail every item regardless of looping.
    logger.debug(f"semantic_dedup: BEFORE embed_texts() ({len(items)} item(s))")
    t0 = time.perf_counter()
    try:
        all_vectors, all_tokens = embed_texts(
            [f"{item['title']}\n\n{item['text']}"[:_MAX_EMBED_CHARS] for item in items]
        )
        logger.debug(f"semantic_dedup: AFTER embed_texts() ({time.perf_counter() - t0:.3f}s)")
    except Exception as e:
        error_msg = f"embed failed, item passed through unfiltered: {e}"
        logger.warning(f"semantic_dedup: batch embed failed, all {len(items)} item(s) passed through unfiltered: {e}")
        record_embedding_failure("semantic_dedup", run_id, len(items), str(e))
        return list(items), [
            NodeCost(
                node_name="semantic_dedup", input_tokens=0, output_tokens=0,
                cost_usd=0.0, latency_ms=0.0,
                error=error_msg,
            )
            for _ in items
        ]

    drop_records: list[dict] = []

    for item, vector, tokens in zip(items, all_vectors, all_tokens):
        cost_usd = round(tokens * COST_PER_TOKEN_USD, 8)
        item_is_roundup = _is_roundup_item(item)

        cross_run_match = None
        for entry in window:
            sim = cosine_similarity(vector, entry["embedding_vector"])
            if sim >= _THRESHOLD:
                match_type = "dedup"
            elif sim >= _CONTENT_OVERLAP_THRESHOLD and not item_is_roundup and not entry.get("is_roundup", False):
                match_type = "content_overlap"
            else:
                continue
            if cross_run_match is None or sim > cross_run_match[1]:
                cross_run_match = (entry, sim, match_type)

        if cross_run_match is not None:
            entry, sim, match_type = cross_run_match
            drop_records.append(_drop_record(item["url"], sim, entry["url"], run_id, filter_type=match_type))
            tier_label = "near-verbatim" if match_type == "dedup" else "content-overlap"
            costs.append(NodeCost(
                node_name="semantic_dedup", input_tokens=tokens, output_tokens=0,
                cost_usd=cost_usd, latency_ms=0.0,
                error=f"dropped as {tier_label} duplicate of previously-seen {entry['url']} (cosine={sim:.3f})",
            ))
            continue

        within_run_match = None
        for idx, sv in enumerate(survivor_vectors):
            sim = cosine_similarity(vector, sv)
            if sim >= _THRESHOLD:
                match_type = "dedup"
            elif sim >= _CONTENT_OVERLAP_THRESHOLD and not item_is_roundup and not _is_roundup_item(survivors[idx]):
                match_type = "content_overlap"
            else:
                continue
            if within_run_match is None or sim > within_run_match[1]:
                within_run_match = (idx, sim, match_type)

        if within_run_match is None:
            survivors.append(item)
            survivor_vectors.append(vector)
            costs.append(NodeCost(
                node_name="semantic_dedup", input_tokens=tokens, output_tokens=0,
                cost_usd=cost_usd, latency_ms=0.0,
            ))
        else:
            idx, sim, match_type = within_run_match
            existing = survivors[idx]
            tier_label = "near-verbatim" if match_type == "dedup" else "content-overlap"
            # Earliest-published wins -- not fuller text (verbosity isn't
            # quality). Ties (identical fetched_at) keep the existing
            # survivor, matching _pick_representative's tie-break style
            # elsewhere in the pipeline. Same tie-break for both tiers --
            # "first-seen" (confirmed decision, 2026-07-23) means earliest
            # published, not earliest processed, matching this existing
            # near-verbatim behavior rather than introducing a second rule.
            if item["fetched_at"] < existing["fetched_at"]:
                dropped_url = existing["url"]
                survivors[idx] = item
                survivor_vectors[idx] = vector
                drop_records.append(_drop_record(dropped_url, sim, item["url"], run_id, filter_type=match_type))
                costs.append(NodeCost(
                    node_name="semantic_dedup", input_tokens=tokens, output_tokens=0,
                    cost_usd=cost_usd, latency_ms=0.0,
                    error=f"kept over {tier_label} duplicate {dropped_url} (cosine={sim:.3f}, published earlier)",
                ))
            else:
                drop_records.append(_drop_record(item["url"], sim, existing["url"], run_id, filter_type=match_type))
                costs.append(NodeCost(
                    node_name="semantic_dedup", input_tokens=tokens, output_tokens=0,
                    cost_usd=cost_usd, latency_ms=0.0,
                    error=f"dropped as {tier_label} duplicate of {existing['url']} (cosine={sim:.3f})",
                ))

    # Two batched store.batch() calls covering every drop/survivor at
    # once, instead of one store.put() per item -- same fix as
    # filter_unseen/mark_seen (2026-07-17), applied to the two per-item
    # write loops named specifically as still-unbatched.
    if drop_records:
        logger.debug(f"semantic_dedup: BEFORE store.batch() (drop_records, {len(drop_records)} record(s))")
        t0 = time.perf_counter()
        store.batch([PutOp(_DROPS_NAMESPACE, str(uuid.uuid4()), record) for record in drop_records])
        logger.debug(f"semantic_dedup: AFTER store.batch() (drop_records) ({time.perf_counter() - t0:.3f}s)")

    if survivors:
        scored_at = datetime.now(timezone.utc).isoformat()
        logger.debug(f"semantic_dedup: BEFORE store.batch() (survivors, {len(survivors)} record(s))")
        t0 = time.perf_counter()
        store.batch([
            PutOp(_NAMESPACE, item["url"], {
                "item_id": item["url"],
                "url": item["url"],
                "embedding_vector": vector,
                "fetched_at": item["fetched_at"],
                "scored_at": scored_at,
                "is_roundup": _is_roundup_item(item),
            })
            for item, vector in zip(survivors, survivor_vectors)
        ])
        logger.debug(f"semantic_dedup: AFTER store.batch() (survivors) ({time.perf_counter() - t0:.3f}s)")

    return survivors, costs
