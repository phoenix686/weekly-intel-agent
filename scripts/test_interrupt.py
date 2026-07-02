"""
Before interrupt
After first invoke (should show interrupt info, not final value): {'value': 'start', '__interrupt__': [Interrupt(value={'question': 'approve or reject?'}, id='2de6271f2d039e01f92f765a88a92993')]}
Before interrupt
Resumed with decision: approve
After resume: {'value': 'got: approve'}
"""
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from typing import TypedDict
from checkpointer_config import get_checkpointer

class TestState(TypedDict):
    value: str

def pause_node(state: TestState) -> dict:
    print("Before interrupt")
    decision = interrupt({"question": "approve or reject?"})
    print(f"Resumed with decision: {decision}")
    return {"value": f"got: {decision}"}

graph = StateGraph(TestState)
graph.add_node("pause_node", pause_node)
graph.add_edge(START, "pause_node")
graph.add_edge("pause_node", END)

compiled = graph.compile(checkpointer=get_checkpointer())

config = {"configurable": {"thread_id": "interrupt-test-1"}}

# First call — should pause at interrupt()
result = compiled.invoke({"value": "start"}, config=config)
print("After first invoke (should show interrupt info, not final value):", result)

# Resume — this is the part we're actually testing
resumed = compiled.invoke(Command(resume="approve"), config=config)
print("After resume:", resumed)