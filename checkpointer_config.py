import os
from langgraph.checkpoint.postgres import PostgresSaver
from connection_pool import get_connection_pool

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

DEFAULT_RECURSION_LIMIT = 50


def get_checkpointer() -> PostgresSaver:
    checkpointer = PostgresSaver(get_connection_pool())
    checkpointer.setup()
    return checkpointer
