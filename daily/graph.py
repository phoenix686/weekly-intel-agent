from langgraph.graph import StateGraph, START, END
from state import DailyGraphState
from discovery.graph import build_discovery_subgraph
from daily.nodes.assemble_digest import assemble_digest
from daily.nodes.send_telegram_digest import send_telegram_digest


def build_daily_graph():
    discovery = build_discovery_subgraph()  # route_sources reads state["source_context"] at runtime
    graph = StateGraph(DailyGraphState)
    graph.add_node("discovery_subgraph", discovery)
    graph.add_node("assemble_digest", assemble_digest)
    graph.add_node("send_telegram_digest", send_telegram_digest)

    graph.add_edge(START, "discovery_subgraph")
    graph.add_edge("discovery_subgraph", "assemble_digest")
    graph.add_edge("assemble_digest", "send_telegram_digest")
    graph.add_edge("send_telegram_digest", END)

    return graph
