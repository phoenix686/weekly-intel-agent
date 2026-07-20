"""
Synthetic test for update_profile's rejection_event writes, YAML preference
update, and run_summary persistence.

Constructs a fake SundayGraphState with 3 classified items (2 rejected
proposals, 1 approved plan_item) and calls update_profile() directly.

Prints:
  - rejection_events queried back from the store
  - run_summary queried back from the store
  - before/after content of data/taste_profile.yaml
  - NodeCost records returned

Run: uv run --env-file .env python scripts/test_update_profile_rejections.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sunday.nodes.update_profile import update_profile, TASTE_PROFILE_PATH
from sunday.memory_store_config import get_store

RUN_ID = "test-update-profile-1"

CLASSIFIED_ITEMS = [
    {
        "url": "https://twitter.com/synthetic/status/1000000000000000003",
        "title": "Build a personal second-brain agent",
        "text": (
            "Thread on building a personal 'second brain' agent that ingests Kindle highlights, "
            "voice memos, and browser bookmarks into a single searchable memory store, with "
            "weekly digest generation via a scheduled agent."
        ),
        "keep": True,
        "reasoning": "Describes a full standalone project concept with no existing tracked equivalent.",
        "tags": ["agentic-engineering", "memory-systems"],
        "classification": "project_proposal",
        "proposal_type": "new",
        "classification_reasoning": (
            "No matched card; describes a complete new autonomous agent project. "
            "This is new scope, not routine reading."
        ),
        "matched_card_id": None,
    },
    {
        "url": "https://twitter.com/synthetic/status/1000000000000000005",
        "title": "Extend bookmark agent with Anki flashcard generation",
        "text": (
            "Proposal-style thread: what if bookmark curation agents could also auto-generate "
            "flashcards from kept items using spaced repetition, exportable to Anki? "
            "Includes a rough architecture sketch."
        ),
        "keep": True,
        "reasoning": "Concrete new-feature concept that could extend the bookmark/discovery agent.",
        "tags": ["agentic-engineering", "learning-resource"],
        "classification": "project_proposal",
        "proposal_type": "extend",
        "classification_reasoning": (
            "Matched card 'weekly-intel' but proposes a new output modality (flashcards/Anki) "
            "beyond what the current pipeline does — genuine scope expansion."
        ),
        "matched_card_id": "6a1c2e014f36a130e02dab6b",
    },
    {
        "url": "https://twitter.com/synthetic/status/1000000000000000006",
        "title": "DSPy vs GEPA vs manual prompt engineering comparison",
        "text": (
            "Detailed writeup comparing DSPy, GEPA, and manual prompt engineering across "
            "three benchmark tasks, with cost and latency numbers for each approach."
        ),
        "keep": True,
        "reasoning": "Directly applicable to planned prompt-optimization work, high-quality comparative resource.",
        "tags": ["evals", "learning-resource"],
        "classification": "plan_item",
        "proposal_type": None,
        "classification_reasoning": "Standalone technical reference, no new project scope.",
        "matched_card_id": None,
    },
]

APPROVAL_RESULTS = [
    {"item_id": "https://twitter.com/synthetic/status/1000000000000000003", "decision": "reject"},
    {"item_id": "https://twitter.com/synthetic/status/1000000000000000005", "decision": "reject"},
]

state = {
    "run_id": RUN_ID,
    "scored_items": [],
    "trello_cards": [],
    "correlated_items": [],
    "classified_items": CLASSIFIED_ITEMS,
    "plan_text": "",
    "pending_approvals": [
        CLASSIFIED_ITEMS[0],
        CLASSIFIED_ITEMS[1],
    ],
    "approval_results": APPROVAL_RESULTS,
    "costs": [
        {"node_name": "score", "input_tokens": 800, "output_tokens": 400,
         "cost_usd": 0.0007, "latency_ms": 1200.0},
        {"node_name": "classify_item", "input_tokens": 500, "output_tokens": 200,
         "cost_usd": 0.00038, "latency_ms": 900.0},
    ],
    "errors": [],
}

# ── capture YAML before ───────────────────────────────────────────────────────
yaml_before = (
    TASTE_PROFILE_PATH.read_text(encoding="utf-8")
    if TASTE_PROFILE_PATH.exists()
    else "(file does not exist yet)"
)

print("=" * 60)
print("YAML BEFORE:")
print(yaml_before)
print("=" * 60)

# ── run the node ──────────────────────────────────────────────────────────────
print("\nCalling update_profile()...")
result = update_profile(state)

# ── costs returned ───────────────────────────────────────────────────────────
print("\nNodeCosts returned:")
for cost in result["costs"]:
    print(f"  {cost['node_name']}: input={cost['input_tokens']} output={cost['output_tokens']} "
          f"cost=${cost['cost_usd']:.6f} latency={cost['latency_ms']:.1f}ms")

# ── query rejection_events back ───────────────────────────────────────────────
store = get_store()
rejection_events = store.search(("weekly_intel", "rejection_events"), limit=50)

print(f"\nrejection_events in store ({len(rejection_events)} total for this namespace):")
for item in rejection_events:
    v = item.value
    if v.get("run_id") == RUN_ID:
        print(f"  [THIS RUN] key={item.key}")
        print(f"    item_id: {v['item_id']}")
        print(f"    proposal_type: {v['proposal_type']}")
        print(f"    content_summary: {v['content_summary'][:80]}...")
        print(f"    timestamp: {v['timestamp']}")
    else:
        print(f"  [prior run={v.get('run_id', '?')}] key={item.key} item_id={v.get('item_id','?')}")

# ── cost_log.csv tail ─────────────────────────────────────────────────────────
cost_log = Path("data/cost_log.csv")
print(f"\ndata/cost_log.csv (last 5 rows):")
if cost_log.exists():
    rows = cost_log.read_text(encoding="utf-8").strip().splitlines()
    for row in rows[-5:]:
        print(f"  {row}")
else:
    print("  (file not found)")

# ── YAML after ────────────────────────────────────────────────────────────────
yaml_after = (
    TASTE_PROFILE_PATH.read_text(encoding="utf-8")
    if TASTE_PROFILE_PATH.exists()
    else "(file still does not exist)"
)

print("\n" + "=" * 60)
print("YAML AFTER:")
print(yaml_after)
print("=" * 60)
