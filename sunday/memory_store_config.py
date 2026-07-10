from langgraph.store.postgres import PostgresStore
from connection_pool import get_connection_pool


def get_store() -> PostgresStore:
    store = PostgresStore(conn=get_connection_pool())
    store.setup()
    return store
