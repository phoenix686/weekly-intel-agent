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
    """A RawItem after URL-heuristic deduplication. One record per unique
    normalized URL; duplicate_count tracks how many raw_items were collapsed."""

    url: str
    title: str
    text: str
    author_name: str
    author_handle: str
    fetched_at: str
    is_thread: bool
    thread_contents: str | None
    expanded_urls: list[str]
    source: str
    duplicate_count: int


class ScoredItem(TypedDict):
    """A ClusteredItem after scoring against the taste profile."""

    url: str
    title: str
    text: str
    author_name: str
    author_handle: str
    fetched_at: str
    is_thread: bool
    thread_contents: str | None
    expanded_urls: list[str]
    source: str
    duplicate_count: int
    keep: bool
    reasoning: str      # one-sentence rationale, visible in LangSmith traces
    tags: list[str]     # 1-3 tags from the fixed vocabulary


class NodeCost(TypedDict):
    """Per-node cost/metrics record. Logged from Phase 0 onward per the
    'cost/metrics from day one, not bolted on later' principle -- even
    though token counts will be 0 until real LLM calls exist (Phase 1+)."""

    node_name: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float


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


class DailyGraphState(TypedDict):
    """State for the daily parent graph.

    Keys shared with DiscoverySubgraphState (run_id, scored_items, costs,
    errors) are passed through to/from the discovery subgraph by name
    intersection. Keys only in DiscoverySubgraphState (raw_items,
    clustered_items, stage) stay internal to the subgraph.
    """

    run_id: str
    scored_items: list[ScoredItem]
    costs: Annotated[list[NodeCost], operator.add]
    errors: list[str]
    digest_text: str        # populated by assemble_digest, consumed by send_telegram_digest


def make_daily_initial_state(run_id: str) -> DailyGraphState:
    return DailyGraphState(
        run_id=run_id,
        scored_items=[],
        costs=[],
        errors=[],
        digest_text="",
    )


class SundayGraphState(TypedDict):
    """State for the Sunday parent graph.

    Pipeline: read_trello -> correlate_trello -> classify_item -> assemble_plan
              -> await_approval (Part B) -> write_outputs -> update_profile

    Keys shared with DiscoverySubgraphState (run_id, scored_items, costs, errors)
    pass through the discovery subgraph by name intersection.
    """

    run_id: str
    scored_items: list[ScoredItem]
    trello_cards: list[dict]        # raw card data from read_trello
    correlated_items: list[dict]    # scored_items + matched_card_id: str | None
    classified_items: list[dict]    # + classification: "plan_item" | "project_proposal"
                                    #   + proposal_type: "extend" | "new" | None
    plan_text: str                  # populated by assemble_plan
    pending_approvals: list[dict]   # project_proposal items awaiting await_approval
    approval_results: list[dict]    # populated by await_approval (Part B)
    costs: Annotated[list[NodeCost], operator.add]          
    errors: list[str]


def make_sunday_initial_state(run_id: str) -> SundayGraphState:
    return SundayGraphState(
        run_id=run_id,
        scored_items=[],
        trello_cards=[],
        correlated_items=[],
        classified_items=[],
        plan_text="",
        pending_approvals=[],
        approval_results=[],
        costs=[],
        errors=[],
    )
