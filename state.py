"""
Shared state schema(s) for the LangGraph Weekly Intelligence Agent.

Phase 0 scope: define the DiscoverySubgraphState only — the schema for the
shared discovery subgraph (search/scrape -> cluster/dedupe -> score), per
spec section 3. Daily/Sunday path-level state (Trello correlation, plan
assembly, approval interrupts) is explicitly NOT defined here yet; that
belongs to later phases once the discovery subgraph itself is validated.

This file has no LLM calls and no I/O. It exists purely as the typed
contract that nodes read from and write to.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict, Literal


class RawItem(TypedDict):
    """A single piece of content as it comes out of search/scrape, before
    any clustering or scoring has happened."""

    source: str           # e.g. "twillot_bootstrap", "web_search"
    url: str
    title: str            # first ~80 chars of text, word-truncated
    text: str             # full unprocessed content, used for clustering+scoring
    fetched_at: str       # ISO timestamp
    author_name: str
    author_handle: str
    is_thread: bool       # True when item quotes/threads another tweet
    thread_contents: str | None  # quoted tweet text, or None
    expanded_urls: list[str]     # expanded link URLs extracted from the item


class ClusteredItem(TypedDict):
    """A RawItem after dedupe/clustering. cluster_id groups near-duplicates;
    representative items keep is_representative=True."""

    item: RawItem
    cluster_id: str
    is_representative: bool


class ScoredItem(TypedDict):
    """A ClusteredItem after scoring against the taste profile."""

    item: RawItem
    cluster_id: str
    score: float            # 0.0-1.0, taste-profile match
    score_reason: str        # short rationale string, for traceability/eval


class NodeCost(TypedDict):
    """Per-node cost/metrics record. Logged from Phase 0 onward per the
    'cost/metrics from day one, not bolted on later' principle -- even
    though token counts will be 0 until real LLM calls exist (Phase 1+)."""

    node_name: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class DiscoverySubgraphState(TypedDict):
    """State threaded through the discovery subgraph.

    Pipeline: search/scrape -> cluster/dedupe -> score
    Each stage reads the previous stage's output list and writes its own.
    Earlier-stage lists are retained (not overwritten) so the full pipeline
    is inspectable after a run -- this matters for the Phase 1 eval harness
    and for debugging via LangSmith traces.
    """

    # stage outputs (populated progressively as the graph runs)
    raw_items: Annotated[list[RawItem], operator.add]
    clustered_items: list[ClusteredItem]
    scored_items: list[ScoredItem]

    # bookkeeping
    run_id: str
    stage: Literal["start", "searched", "clustered", "scored", "done"]
    costs: Annotated[list[NodeCost], operator.add]
    errors: list[str]
