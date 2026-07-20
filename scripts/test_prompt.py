import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logging_config import setup_logging
setup_logging()

from discovery.nodes.score import score_node

KEYWORDS = ["harness", "fde", "forward deployed", "certification", "architect"]

# --- Step 1: filter scored_items.json to the suspect false-drops ---

with open("data/scored_items.json", encoding="utf-8") as f:
    scored_items = json.load(f)

subset = [
    item for item in scored_items
    if not item["keep"]
    and any(
        kw in (item.get("title", "") + " " + item.get("text", "")).lower()
        for kw in KEYWORDS
    )
]

with open("data/test_subset.json", "w", encoding="utf-8") as f:
    json.dump(subset, f, ensure_ascii=False, indent=2)

print(f"Found {len(subset)} dropped items matching keywords\n")
for item in subset:
    print(f"TITLE: {item['title'][:80]}")
    print(f"  was: keep=False | {item['reasoning']}")
    print()

if not subset:
    print("No matching items — nothing to re-score.")
    sys.exit(0)

# --- Step 2: re-score subset with current prompt ---

print("=" * 60)
print("Re-scoring with current prompt...\n")

state = {"clustered_items": subset}
result = score_node(state)

for old, new in zip(subset, result["scored_items"]):
    changed = old["keep"] != new["keep"]
    marker = "*** CHANGED ***" if changed else "(same)"
    print(f"{marker}")
    print(f"  TITLE: {old['title'][:80]}")
    print(f"  BEFORE: keep={old['keep']} | {old['reasoning']}")
    print(f"  AFTER:  keep={new['keep']} | {new['reasoning']}")
    print()

comparisons = [
    {
        "title": old["title"],
        "url": old["url"],
        "before": {"keep": old["keep"], "reasoning": old["reasoning"], "tags": old["tags"]},
        "after":  {"keep": new["keep"], "reasoning": new["reasoning"], "tags": new["tags"]},
        "changed": old["keep"] != new["keep"],
    }
    for old, new in zip(subset, result["scored_items"])
]

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out_path = f"data/test_results_{ts}.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"cost": result["costs"][0], "comparisons": comparisons}, f, ensure_ascii=False, indent=2)

print(f"Cost: {result['costs'][0]}")
print(f"Saved to {out_path}")
