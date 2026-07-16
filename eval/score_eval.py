"""
Manual eval script for discovery/nodes/score.py's score_node -- per
closeout-spec.md Section 3. Not scheduled, not a LangGraph node, no
state-schema involvement. Run by hand whenever there's a real labeled
sample to check score_node's real prompt against.

Scope: score_node only, not classify_item -- score_node is the foundation
every downstream judgment depends on, and already has the full 510-item
bootstrap run (data/scored_items.json) to draw labeled examples from.

eval/labeled_set.json format: a list of
    {"item_id": <url>, "correct_decision": "keep"|"drop",
     "correct_tags": [...], "notes": "..."}
one entry per hand-labeled item. item_id must match a "url" in
data/scored_items.json (or whatever SCORED_ITEMS_PATH points at) so this
script can look up that item's real title/text to re-score.

IMPORTANT (CLAUDE.md Section 8's standing exception): the actual label
CONTENT -- which items are correct, which tags are right -- is Pooja's
judgment call, not this script's or Claude Code's. eval/labeled_set.json
ships empty. This script's own accuracy numbers are meaningless until
real labels are added; it exists so that the moment real labels exist,
running it produces a real number immediately, nothing else to build.

Run: uv run --env-file .env python eval/score_eval.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# _score_batch is score_node's own real scoring call (same prompt, same
# Haiku model) -- reused directly rather than re-implemented, so this eval
# can never silently drift from what actually runs in production.
from discovery.nodes.score import _score_batch

LABELED_SET_PATH = Path(__file__).parent / "labeled_set.json"
SCORED_ITEMS_PATH = Path(__file__).parent.parent / "data" / "scored_items.json"


def load_labeled_set(path: Path = LABELED_SET_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_source_items_by_url(path: Path = SCORED_ITEMS_PATH) -> dict[str, dict]:
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    return {item["url"]: item for item in items}


def run_eval(labeled_set: list[dict], source_by_url: dict[str, dict]) -> dict:
    """Re-runs score_node's real prompt against every labeled item and
    compares actual vs. expected keep/drop + tag overlap. Raises if a
    labeled_set.json entry references an item_id missing from the source
    data -- a silent skip would quietly under-count accuracy instead of
    surfacing a real data-mismatch bug."""
    missing = [e["item_id"] for e in labeled_set if e["item_id"] not in source_by_url]
    if missing:
        raise ValueError(
            f"labeled_set.json references item_id(s) not found in {SCORED_ITEMS_PATH.name}: {missing}"
        )

    batch = [source_by_url[entry["item_id"]] for entry in labeled_set]
    rescored, input_tokens, output_tokens = _score_batch(batch, offset=0, run_id="score_eval")
    rescored_by_url = {item["url"]: item for item in rescored}

    results = []
    keep_correct = 0
    tag_overlaps = []
    for entry in labeled_set:
        actual = rescored_by_url[entry["item_id"]]
        expected_keep = entry["correct_decision"] == "keep"
        keep_match = actual["keep"] == expected_keep
        keep_correct += int(keep_match)

        actual_tags = set(actual["tags"])
        expected_tags = set(entry["correct_tags"])
        overlap = len(actual_tags & expected_tags) / len(expected_tags) if expected_tags else None
        if overlap is not None:
            tag_overlaps.append(overlap)

        results.append({
            "item_id": entry["item_id"],
            "expected_decision": entry["correct_decision"],
            "actual_keep": actual["keep"],
            "keep_match": keep_match,
            "expected_tags": sorted(expected_tags),
            "actual_tags": sorted(actual_tags),
            "tag_overlap": overlap,
        })

    n = len(labeled_set)
    return {
        "n_items": n,
        "keep_accuracy": keep_correct / n if n else None,
        "mean_tag_overlap": sum(tag_overlaps) / len(tag_overlaps) if tag_overlaps else None,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "results": results,
    }


def main():
    labeled_set = load_labeled_set()
    if not labeled_set:
        print(
            f"{LABELED_SET_PATH} is empty -- add real hand-labeled entries "
            "before this script produces a meaningful accuracy number."
        )
        return

    source_by_url = load_source_items_by_url()
    report = run_eval(labeled_set, source_by_url)

    print(json.dumps(report, indent=2))
    print(f"\nkeep_accuracy: {report['keep_accuracy']:.2%}  ({report['n_items']} labeled item(s))")
    if report["mean_tag_overlap"] is not None:
        print(f"mean_tag_overlap: {report['mean_tag_overlap']:.2%}")


if __name__ == "__main__":
    main()
