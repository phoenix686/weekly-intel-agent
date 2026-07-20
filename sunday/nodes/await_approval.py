import hashlib
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Send

from telegram.bot_client import send_message
from telegram.markdown import escape_markdown_v2
from core.checkpointer_config import get_checkpointer
from sunday.memory_store_config import get_store


class ProposalState(TypedDict):
    proposal_id: str
    decision: str | None
    message_id: str | None
    run_id: str
    url: str
    text: str
    title: str
    tags: list[str]
    reasoning: str
    classification_reasoning: str
    proposal_type: str | None
    matched_card_id: str | None


def thread_id_for(proposal_id: str) -> str:
    return "proposal-" + hashlib.sha256(proposal_id.encode()).hexdigest()[:16]


def _format_proposal_message(state: ProposalState) -> str:
    title = state.get("title") or state["text"][:80]
    tags = " ".join(f"`{escape_markdown_v2(t)}`" for t in state.get("tags", []))
    kind = "🔄 *Extend Existing Project*" if state.get("proposal_type") == "extend" else "🆕 *New Project Proposal*"
    return (
        f"{kind}\n\n"
        f"[{escape_markdown_v2(title)}]({state['url']})\n"
        f"Tags: {tags}\n\n"
        f"_{escape_markdown_v2(state['reasoning'])}_\n\n"
        f"Reply \"approve\" or \"reject\" to this message\\."
    )


def send_proposal_message(state: ProposalState) -> dict:
    response = send_message(_format_proposal_message(state), parse_mode="MarkdownV2")
    real_message_id = response["result"]["message_id"]
    return {"message_id": real_message_id}


def await_confirmation(state: ProposalState) -> dict:
    decision = interrupt({"proposal_id": state["proposal_id"]})
    return {"decision": decision}


_child_graph = None


def get_proposal_graph():
    global _child_graph
    if _child_graph is None:
        g = StateGraph(ProposalState)
        g.add_node("send_proposal_message", send_proposal_message)
        g.add_node("await_confirmation", await_confirmation)
        g.add_edge(START, "send_proposal_message")
        g.add_edge("send_proposal_message", "await_confirmation")
        g.add_edge("await_confirmation", END)
        _child_graph = g.compile(checkpointer=get_checkpointer())
    return _child_graph


def route_to_approvals(state) -> list[Send]:
    return [
        Send("proposal_worker", {
            "proposal_id": p["url"],
            "decision": None,
            "message_id": None,
            "run_id": state["run_id"],
            **p,
        })
        for p in state["pending_approvals"]
    ]


def proposal_worker(state: ProposalState) -> dict:
    thread_id = thread_id_for(state["proposal_id"])
    child = get_proposal_graph()
    result = child.invoke(state, config={"configurable": {"thread_id": thread_id}})

    store = get_store()
    store.put(
        ("weekly_intel", "pending_resume_map"),
        str(result["message_id"]),
        {
            "thread_id": thread_id,
            "proposal_id": state["proposal_id"],
            "run_id": state["run_id"],
        },
    )

    return {"pending_resumes": [{
        "proposal_id": state["proposal_id"],
        "thread_id": thread_id,
        "message_id": result.get("message_id"),
    }]}
