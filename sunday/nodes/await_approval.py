from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Send
from telegram.bot_client import send_message


class ProposalState(TypedDict):
    proposal_id: str
    decision: str | None
    url: str
    text: str
    tags: list[str]
    reasoning: str
    proposal_type: str | None
    matched_card_id: str | None


def _format_proposal_message(state: ProposalState) -> str:
    title = state.get("title") or state["text"][:80]
    tags = " ".join(f"`{t}`" for t in state.get("tags", []))
    kind = "🔄 *Extend Existing Project*" if state.get("proposal_type") == "extend" else "🆕 *New Project Proposal*"
    return (
        f"{kind}\n\n"
        f"[{title}]({state['url']})\n"
        f"Tags: {tags}\n\n"
        f"_{state['reasoning']}_\n\n"
        f"Reply \"approve\" or \"reject\" to this message."
    )


def send_proposal_message(state: ProposalState) -> dict:
    send_message(_format_proposal_message(state))
    return {}


def await_confirmation(state: ProposalState) -> dict:
    decision = interrupt({"proposal_id": state["proposal_id"]})
    return {"decision": decision}


_proposal_subgraph = StateGraph(ProposalState)
_proposal_subgraph.add_node("send_proposal_message", send_proposal_message)
_proposal_subgraph.add_node("await_confirmation", await_confirmation)
_proposal_subgraph.add_edge(START, "send_proposal_message")
_proposal_subgraph.add_edge("send_proposal_message", "await_confirmation")
_proposal_subgraph.add_edge("await_confirmation", END)
_compiled_proposal_subgraph = _proposal_subgraph.compile()


def route_to_approvals(state) -> list[Send]:
    return [
        Send("proposal_worker", {"proposal_id": p["url"], "decision": None, **p})
        for p in state["pending_approvals"]
    ]


def proposal_worker(state: ProposalState) -> dict:
    final = _compiled_proposal_subgraph.invoke(state)
    return {"approval_results": [{"item_id": final["proposal_id"], "decision": final["decision"]}]}