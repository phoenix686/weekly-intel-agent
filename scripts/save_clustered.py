from core.logging_config import setup_logging
setup_logging()

from discovery.nodes.ingest_bookmarks import ingest_bookmarks
from discovery.nodes.cluster_dedupe import cluster_dedupe_node
import json

state = {"raw_items": [], "costs": [], "errors": [], "clustered_items": []}
state.update(ingest_bookmarks(state))
state.update(cluster_dedupe_node(state))

with open("data/clustered_items.json", "w", encoding="utf-8") as f:
    json.dump(state["clustered_items"], f, ensure_ascii=False, indent=2)

print(f"Saved {len(state['clustered_items'])} clustered items")
