import hashlib
import time
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Send

from telegram.bot_client import send_message
from telegram.markdown import escape_markdown_v2
from checkpointer_config import get_checkpointer
from sunday.memory_store_config import get_store
from discovery.candidate_discovery import find_candidates
from state import NodeCost

DISCOVERY_QUERY = "AI agent engineering newsletter blog"


class SourceProposalState(TypedDict):
    proposal_id: str
    decision: str | None
    message_id: str | None
    run_id: str
    name: str
    feed_url: str
    bucket: str
    sample_keep_rate: float
    sample_reasoning: str


def thread_id_for(proposal_id: str) -> str:
    return "source-proposal-" + hashlib.sha256(proposal_id.encode()).hexdigest()[:16]


def _format_source_proposal_message(state: SourceProposalState) -> str:
    bucket_label = "Daily" if state["bucket"] == "daily" else "Sunday-only"
    return (
        f"🆕 *New Source Proposal*\n\n"
        f"[{escape_markdown_v2(state['name'])}]({state['feed_url']})\n"
        f"Would join: `{bucket_label}` sources\n\n"
        f"_{escape_markdown_v2(state['sample_reasoning'])}_\n\n"
        f"Sampled keep rate: {state['sample_keep_rate']:.0%}\n\n"
        f"Reply \"approve\" or \"reject\" to this message\\."
    )


def send_source_proposal_message(state: SourceProposalState) -> dict:
    response = send_message(_format_source_proposal_message(state), parse_mode="MarkdownV2")
    real_message_id = response["result"]["message_id"]
    return {"message_id": real_message_id}


def await_source_confirmation(state: SourceProposalState) -> dict:
    decision = interrupt({"proposal_id": state["proposal_id"]})
    return {"decision": decision}


_child_graph = None


def get_source_proposal_graph():
    global _child_graph
    if _child_graph is None:
        g = StateGraph(SourceProposalState)
        g.add_node("send_source_proposal_message", send_source_proposal_message)
        g.add_node("await_source_confirmation", await_source_confirmation)
        g.add_edge(START, "send_source_proposal_message")
        g.add_edge("send_source_proposal_message", "await_source_confirmation")
        g.add_edge("await_source_confirmation", END)
        _child_graph = g.compile(checkpointer=get_checkpointer())
    return _child_graph


def discover_sources(state) -> dict:
    """Sunday-cycle node: search for candidate new sources, sample+score
    them against taste_profile.yaml via the existing score_node (no new
    scoring mechanism), and surface the ones worth proposing. The actual
    Telegram fan-out happens in the conditional edge (route_to_source_discovery)
    that follows this node, mirroring classify_item -> _fan_out_after_classify's
    two-step shape."""
    t0 = time.perf_counter()
    candidates = find_candidates(DISCOVERY_QUERY)
    cost = NodeCost(
        node_name="discover_sources",
        input_tokens=0, output_tokens=0,
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        cost_usd=0.0,  # real score_node cost is already logged from inside find_candidates' own calls
    )
    return {"pending_source_candidates": candidates, "costs": [cost]}


def route_to_source_discovery(state) -> list[Send]:
    return [
        Send("source_proposal_worker", {
            "proposal_id": c["feed_url"],
            "decision": None,
            "message_id": None,
            "run_id": state["run_id"],
            **c,
        })
        for c in state["pending_source_candidates"]
    ]


def source_proposal_worker(state: SourceProposalState) -> dict:
    thread_id = thread_id_for(state["proposal_id"])
    child = get_source_proposal_graph()
    result = child.invoke(state, config={"configurable": {"thread_id": thread_id}})

    store = get_store()
    store.put(
        ("weekly_intel", "pending_source_resume_map"),
        str(result["message_id"]),
        {
            "thread_id": thread_id,
            "proposal_id": state["proposal_id"],
            "run_id": state["run_id"],
        },
    )

    return {"pending_source_resumes": [{
        "proposal_id": state["proposal_id"],
        "thread_id": thread_id,
        "message_id": result.get("message_id"),
    }]}
