"""
cluster_dedupe_node: URL-heuristic deduplication of raw bookmark items.

Groups RawItems by normalized URL, selects the representative (longest text,
breaking ties by earliest fetched_at), and returns one ClusteredItem per
unique URL. No LangGraph imports, no LLM calls.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from core.state import DiscoverySubgraphState, RawItem, ClusteredItem, NodeCost
from discovery.seen_items import filter_unseen, mark_seen
from discovery.semantic_dedup import dedupe_semantic
from discovery.taste_vectors import taste_prefilter
from core.observability import record_node_summary

logger = logging.getLogger(__name__)


_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_source_platform", "s", "ref", "fbclid", "gclid",
    "mc_cid", "mc_eid", "_hsenc", "_hsmi", "hs_email", "hs_automation",
})


def _normalize_url(url: str) -> str:
    """Lowercase, strip trailing slash and fragment, remove tracking query params."""
    if not url:
        return ""
    parsed = urlparse(url.strip().lower())

    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        filtered = {
            k: v for k, v in params.items()
            if k not in _TRACKING_PARAMS and not k.startswith("utm_")
        }
        # Sort keys so param order doesn't create false non-duplicates
        new_query = urlencode(sorted(filtered.items()), doseq=True)
    else:
        new_query = ""

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path.rstrip("/"),
        parsed.params,
        new_query,
        "",  # strip fragment
    ))


def _pick_representative(group: list[RawItem]) -> RawItem:
    """Longest text wins; earliest fetched_at breaks ties."""
    return sorted(group, key=lambda x: (-len(x["text"]), x["fetched_at"]))[0]


def _dedupe(raw_items: list[RawItem]) -> list[ClusteredItem]:
    groups: dict[str, list[RawItem]] = defaultdict(list)
    for item in raw_items:
        groups[_normalize_url(item["url"])].append(item)

    result: list[ClusteredItem] = []
    for group in groups.values():
        rep = _pick_representative(group)
        item = ClusteredItem(
            url=rep["url"],
            title=rep["title"],
            text=rep["text"],
            author_name=rep["author_name"],
            author_handle=rep["author_handle"],
            fetched_at=rep["fetched_at"],
            is_thread=rep["is_thread"],
            thread_contents=rep["thread_contents"],
            expanded_urls=rep["expanded_urls"],
            source=rep["source"],
            duplicate_count=len(group),
        )
        if "has_video" in rep:
            item["has_video"] = rep["has_video"]
        if "video_url" in rep:
            item["video_url"] = rep["video_url"]
        result.append(item)
    return result


_ADHOC_SOURCE = "adhoc_telegram"


def cluster_dedupe_node(state: DiscoverySubgraphState) -> dict:
    t0 = time.perf_counter()
    clustered = _dedupe(state["raw_items"])

    unseen, seen_urls = filter_unseen(clustered)
    if seen_urls:
        logger.info(f"cluster_dedupe: skipped {len(seen_urls)} already-seen item(s): {seen_urls}")

    costs = [NodeCost(
        node_name="cluster_dedupe",
        input_tokens=0,
        output_tokens=0,
        latency_ms=round((time.perf_counter() - t0) * 1000, 4),
        cost_usd=0.0
    )]

    # Ad-hoc items bypass both the semantic dedup and taste pre-filter
    # entirely (batch2-dedup-taste-spec.md Section 10) -- something Pooja
    # personally chose to text the bot about is maximally relevant and
    # opted-in by construction, never embedded, never persisted as a
    # dedup target. A single source-based split here, not a duplicated
    # check inside each filter.
    adhoc_items = [item for item in unseen if item["source"] == _ADHOC_SOURCE]
    filterable_items = [item for item in unseen if item["source"] != _ADHOC_SOURCE]

    run_id = state["run_id"]

    deduped, semantic_costs = dedupe_semantic(filterable_items, run_id)
    costs.extend(semantic_costs)

    relevant, uncategorized, taste_costs = taste_prefilter(deduped, run_id)
    costs.extend(taste_costs)

    # uncategorized items deliberately do NOT join clustered_items -- they
    # never reach score_node (no LLM call spent classifying content
    # already known not to fit ALLOWED_TAGS), but are carried through in
    # their own state field so assemble_digest/assemble_plan can surface
    # them instead of them vanishing. See taste_prefilter's docstring.
    #
    # BUG FIX (2026-07-26, real run 08b5d13b): because uncategorized items
    # never reach score_node, they were also never covered by
    # score_node's mark_seen() call (scoped to all_scored only) --
    # nothing else in the pipeline ever marked them seen either. A
    # below-taste-threshold item that's still fetchable next run (a
    # dormant source's same top-N posts, in particular -- see
    # anthropic_blog.py's new recency filter for the other half of that
    # specific case) would resurface as "new" indefinitely, every run,
    # forever. taste_prefilter is itself the terminal decision for these
    # items this run (there's no later "real processing" to wait for the
    # way score_node's scoring is), so marking them seen right here,
    # immediately after a successful taste_prefilter call, is the direct
    # analogue of mark_seen()'s existing "call once real processing has
    # actually completed" contract. Same dry_run gate as score_node's
    # mark_seen() -- manual/dry runs must not permanently burn through
    # the real seen_items pool for these either.
    if state.get("dry_run", False):
        logger.info(f"dry_run=True -- skipping mark_seen() for {len(uncategorized)} uncategorized item(s)")
    else:
        mark_seen([item["url"] for item in uncategorized])

    clustered_items = relevant + adhoc_items
    record_node_summary(
        run_id=run_id,
        node_name="cluster_dedupe",
        items_in=len(state["raw_items"]),
        items_out=len(clustered_items),
        cost_usd=sum(c["cost_usd"] for c in costs),
        duration_seconds=round(time.perf_counter() - t0, 3),
    )

    return {
        "clustered_items": clustered_items,
        "uncategorized_items": uncategorized,
        "costs": costs,
    }
