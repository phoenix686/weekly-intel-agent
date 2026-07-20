from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from core.state import SundayGraphState
from discovery.graph import build_discovery_subgraph
from core.checkpointer_config import get_checkpointer
from sunday.nodes.read_trello import read_trello
from sunday.nodes.correlate_trello import correlate_trello
from sunday.nodes.classify_item import classify_item
from sunday.nodes.prioritize_plan_items import prioritize_plan_items
from sunday.nodes.assemble_plan import assemble_plan
from sunday.nodes.send_telegram_plan import send_telegram_plan
from sunday.nodes.await_approval import route_to_approvals, proposal_worker
from sunday.nodes.update_profile import update_profile


def _fan_out_after_classify(state: SundayGraphState) -> list[Send]:
    sends = [Send("prioritize_plan_items", state)]
    sends += route_to_approvals(state)
    return sends


def build_sunday_graph():
    discovery = build_discovery_subgraph()  # route_sources reads state["source_context"] at runtime

    graph = StateGraph(SundayGraphState)
    graph.add_node("discovery_subgraph", discovery)
    graph.add_node("read_trello", read_trello)
    graph.add_node("correlate_trello", correlate_trello)
    graph.add_node("classify_item", classify_item)
    graph.add_node("prioritize_plan_items", prioritize_plan_items)
    graph.add_node("assemble_plan", assemble_plan)
    graph.add_node("send_telegram_plan", send_telegram_plan)
    graph.add_node("proposal_worker", proposal_worker)
    graph.add_node("update_profile", update_profile)

    graph.add_edge(START, "discovery_subgraph")
    graph.add_edge("discovery_subgraph", "read_trello")
    graph.add_edge("read_trello", "correlate_trello")
    graph.add_edge("correlate_trello", "classify_item")
    graph.add_conditional_edges("classify_item", _fan_out_after_classify)
    graph.add_edge("prioritize_plan_items", "assemble_plan")
    graph.add_edge("assemble_plan", "send_telegram_plan")
    graph.add_edge("send_telegram_plan", "update_profile")
    graph.add_edge("proposal_worker", "update_profile")
    graph.add_edge("update_profile", END)

    return graph.compile(checkpointer=get_checkpointer())
