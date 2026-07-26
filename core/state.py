"""
Shared state schema(s) for the LangGraph Weekly Intelligence Agent.

Phase 0 scope: define the DiscoverySubgraphState only — the schema for the
shared discovery subgraph (search/scrape -> cluster/dedupe -> score), per
spec section 3. Daily/Saturday path-level state (Trello correlation, plan
assembly, approval interrupts) is explicitly NOT defined here yet; that
belongs to later phases once the discovery subgraph itself is validated.

This file has no LLM calls and no I/O. It exists purely as the typed
contract that nodes read from and write to.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict, Literal, NotRequired


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
    has_video: NotRequired[bool]        # True if a companion video was detected (not fetched/transcribed)
    video_url: NotRequired[str | None]  # companion video URL, if trivially available


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
    has_video: NotRequired[bool]
    video_url: NotRequired[str | None]


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
    has_video: NotRequired[bool]
    video_url: NotRequired[str | None]


class UncategorizedItem(TypedDict):
    """A ClusteredItem the taste-prefilter couldn't match to any existing
    topic tag -- max cosine similarity across every mapped topic vector
    fell below discovery/taste_vectors.py's 0.30 threshold. Bypasses
    score_node entirely (no LLM call spent classifying content already
    known not to fit the current tag vocabulary), but is carried through
    to output instead of silently dropped -- see assemble_digest's and
    assemble_plan's trailing "didn't match any existing topic" section,
    and telegram/feedback_router.py's reuse of the same numbered-reply
    mechanism. best_tag/similarity_score are audit metadata only, NOT
    one of ALLOWED_TAGS -- no new tag vector is created or auto-assigned
    from this; that stays a human (Pooja) decision via a Telegram reply."""

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
    best_tag: str            # closest-matching existing tag, still below threshold
    similarity_score: float  # that tag's cosine similarity
    has_video: NotRequired[bool]
    video_url: NotRequired[str | None]


class NodeCost(TypedDict):
    """Per-node cost/metrics record. Logged from Phase 0 onward per the
    'cost/metrics from day one, not bolted on later' principle -- even
    though token counts will be 0 until real LLM calls exist (Phase 1+)."""

    node_name: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float
    error: NotRequired[str | None]  # non-user-facing failure/drop visibility --
                                     # optional so every existing NodeCost(...)
                                     # call site keeps constructing without it
    provider: NotRequired[str]  # which paid API this cost is attributable to
                                  # ("anthropic", "nvidia") -- 2026-07-26, real
                                  # cost reporting. Optional (defaults to
                                  # unattributed when summed) so the many
                                  # existing zero-cost NodeCost(...) call sites
                                  # (Telegram sends, Trello reads, formatting
                                  # nodes) don't need updating just to keep
                                  # constructing.


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
    uncategorized_items: list[UncategorizedItem]  # populated by cluster_dedupe_node's
                                                    # taste_prefilter call -- items below the
                                                    # 0.30 threshold, never reach score_node

    # bookkeeping
    run_id: str
    costs: Annotated[list[NodeCost], operator.add]
    errors: Annotated[list[str], operator.add]
    source_context: Literal["daily", "saturday"]  # read by route_sources() to
                                                 # decide the active source
                                                 # node(s) for this invocation
    dry_run: bool  # when True, score_node AND cluster_dedupe_node both skip
                    # mark_seen() (scored items and uncategorized items,
                    # respectively) -- lets manual testing exercise the
                    # pipeline without permanently exhausting the real
                    # seen_items pool


class DailyGraphState(TypedDict):
    """State for the daily parent graph.

    Keys shared with DiscoverySubgraphState (run_id, scored_items, costs,
    errors, source_context) are passed through to/from the discovery
    subgraph by name intersection. Keys only in DiscoverySubgraphState
    (raw_items, clustered_items) stay internal to the subgraph.
    """

    run_id: str
    scored_items: list[ScoredItem]
    uncategorized_items: list[UncategorizedItem]  # passed through from DiscoverySubgraphState
                                                    # by name intersection; see assemble_digest's
                                                    # trailing "didn't match any existing topic" section
    costs: Annotated[list[NodeCost], operator.add]
    errors: list[str]
    source_context: Literal["daily", "saturday"]
    digest_text: str        # populated by assemble_digest, consumed by send_telegram_digest
    digest_item_map: dict[int, dict]  # {1: {url, title, tags, reasoning}, ...} -- populated by
                                       # assemble_digest, persisted by send_telegram_digest keyed
                                       # by the sent message_id so a later numbered reply resolves
                                       # (includes uncategorized items too, numbered after kept ones,
                                       # so a reply naming a new tag routes through the same path)


def make_daily_initial_state(run_id: str) -> DailyGraphState:
    return DailyGraphState(
        run_id=run_id,
        scored_items=[],
        uncategorized_items=[],
        costs=[],
        errors=[],
        digest_text="",
        digest_item_map={},
        source_context="daily",
    )


class SaturdayGraphState(TypedDict):
    """State for the Saturday parent graph.

    Pipeline: read_trello -> correlate_trello -> classify_item -> assemble_plan
              -> await_approval (Part B) -> write_outputs -> update_profile

    Keys shared with DiscoverySubgraphState (run_id, scored_items, costs,
    errors, source_context) pass through the discovery subgraph by name
    intersection.
    """

    run_id: str
    scored_items: list[ScoredItem]
    uncategorized_items: list[UncategorizedItem]  # passed through from DiscoverySubgraphState
                                                    # by name intersection; bypasses
                                                    # correlate_trello/classify_item entirely --
                                                    # see assemble_plan's trailing section
    trello_cards: list[dict]        # raw card data from read_trello: card_id, name,
                                    #   desc, list_id, list_name, url, checklist_items,
                                    #   last_activity (Trello dateLastActivity, ISO string)
    card_movements: list[dict]      # populated by read_trello (saturday/card_movement.py):
                                    #   {card_id, previous_list_name, current_list_name,
                                    #   status: "archived"|"not_found"|"completed"|"moved"|"unchanged"}
                                    #   -- real movement since the most recent prior plan_history
                                    #   entry; [] if there's no prior entry to compare against
    correlated_items: list[dict]    # scored_items + matched_card_id: str | None
    classified_items: list[dict]    # + classification: "plan_item" | "project_proposal"
                                    #   + proposal_type: "extend" | "new" | None
    prioritized_project_work: list[dict]  # populated by prioritize_plan_items (after
                                    #   classify_item, before assemble_plan): bounded
                                    #   (<= MAX_PROJECT_WORK_ITEMS), priority-ordered
                                    #   selection -- {matched_card_id, source: "new_item"|
                                    #   "stale_nudge", item_url, priority_reasoning,
                                    #   movement_note}. Not yet rendered by assemble_plan
                                    #   (that's the final sub-phase of this checkpoint).
    plan_text: str                  # populated by assemble_plan
    plan_item_map: dict[int, dict]  # {1: {url, title, tags, reasoning}, ...} -- populated by
                                     # assemble_plan, persisted by send_telegram_plan keyed by
                                     # the sent message_id so a later numbered reply resolves
    pending_approvals: list[dict]   # project_proposal items awaiting await_approval
    pending_resumes: Annotated[list[dict], operator.add]   # one entry per proposal_worker Send: {proposal_id, thread_id, message_id}
    costs: Annotated[list[NodeCost], operator.add]
    errors: list[str]
    source_context: Literal["daily", "saturday"]
    dry_run: bool  # passed through by name intersection into the nested
                    # discovery subgraph; see DiscoverySubgraphState.dry_run


def make_saturday_initial_state(run_id: str, dry_run: bool = False) -> SaturdayGraphState:
    return SaturdayGraphState(
        run_id=run_id,
        scored_items=[],
        uncategorized_items=[],
        trello_cards=[],
        card_movements=[],
        correlated_items=[],
        classified_items=[],
        prioritized_project_work=[],
        plan_text="",
        plan_item_map={},
        pending_approvals=[],
        pending_resumes=[],
        costs=[],
        errors=[],
        source_context="saturday",
        dry_run=dry_run,
    )
