import os
import psycopg
from psycopg.rows import dict_row
from langgraph.store.postgres import PostgresStore


def get_store() -> PostgresStore:
    db_uri = os.environ["DB_URI"]
    conn = psycopg.Connection.connect(
        db_uri, autocommit=True, prepare_threshold=0, row_factory=dict_row
    )
    store = PostgresStore(conn=conn)
    store.setup()
    return store