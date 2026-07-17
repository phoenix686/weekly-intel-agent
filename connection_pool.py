import logging
import os
import time
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

_pool = None


def get_connection_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        logger.info("connection_pool: BEFORE constructing ConnectionPool (first call this process)")
        t0 = time.perf_counter()
        _pool = ConnectionPool(
            conninfo=os.environ["DB_URI"],
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        logger.info(f"connection_pool: AFTER constructing ConnectionPool ({time.perf_counter() - t0:.3f}s)")
    return _pool
