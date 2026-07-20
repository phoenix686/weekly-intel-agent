import logging
import time
from langgraph.store.postgres import PostgresStore
from core.connection_pool import get_connection_pool

logger = logging.getLogger(__name__)


def get_store() -> PostgresStore:
    store = PostgresStore(conn=get_connection_pool())
    logger.debug("memory_store_config: BEFORE store.setup()")
    t0 = time.perf_counter()
    store.setup()
    logger.debug(f"memory_store_config: AFTER store.setup() ({time.perf_counter() - t0:.3f}s)")
    return store
