import logging
import os
import time
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

_pool = None

# Real hang found 2026-07-17: two Sunday runs died silently (zero
# traceback, zero exception) right after cluster_dedupe's log line.
# Read psycopg_pool 3.3.1's actual source (pool.py _add_connection/
# _connect): the pool's own `timeout=30.0` only bounds how long a caller
# waits in queue for an already-open connection -- it does NOT bound how
# long the underlying libpq connect() call itself may take when the pool
# is actually opening a new connection (initial fill or a reconnect after
# a lost one), since _add_connection() calls _connect() with no timeout
# argument. libpq's own default for an unset connect_timeout is "wait
# indefinitely." Setting connect_timeout directly in `kwargs` below (not
# relying on _connect()'s optional override) means it's always present in
# the resolved kwargs dict passed to every real connect() call, regardless
# of which code path opens the connection.
_CONNECT_TIMEOUT_SECONDS = 10  # same order of magnitude as huggingface_hub's own DEFAULT_REQUEST_TIMEOUT (10s)
_STATEMENT_TIMEOUT_SECONDS = 30  # generous for real work, well short of the 45-minute job ceiling


def _configure_connection(conn) -> None:
    """Bounds any single query on this connection -- connect_timeout
    (below) only bounds the TCP handshake; a query that gets past that
    but then stalls server-side (e.g. lock contention) would otherwise
    have no bound of its own. Runs once per newly-opened connection
    (psycopg_pool calls this from _add_connection() every time a new
    connection is created, not just at pool construction)."""
    logger.debug(f"connection_pool: BEFORE configure_connection (SET statement_timeout='{_STATEMENT_TIMEOUT_SECONDS}s')")
    t0 = time.perf_counter()
    conn.execute(f"SET statement_timeout = '{_STATEMENT_TIMEOUT_SECONDS}s'")
    logger.debug(f"connection_pool: AFTER configure_connection ({time.perf_counter() - t0:.3f}s)")


def get_connection_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        logger.debug("connection_pool: BEFORE constructing ConnectionPool (first call this process)")
        t0 = time.perf_counter()
        _pool = ConnectionPool(
            conninfo=os.environ["DB_URI"],
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
                "connect_timeout": _CONNECT_TIMEOUT_SECONDS,
            },
            configure=_configure_connection,
        )
        logger.debug(f"connection_pool: AFTER constructing ConnectionPool ({time.perf_counter() - t0:.3f}s)")
    return _pool
