import os
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool = None


def get_connection_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=os.environ["DB_URI"],
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
    return _pool
