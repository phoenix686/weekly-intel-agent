"""
score-eval-script (Checkpoint 4, closeout-spec.md Section 3): eval/score_eval.py's
accuracy/tag-overlap math, tested against a mocked _score_batch so this
doesn't need a real Anthropic call or real labeled_set.json content --
label CONTENT is Pooja's judgment call (CLAUDE.md Section 8), not
something this test fabricates either; it only exercises the script's
comparison logic with made-up placeholder decisions.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import eval.score_eval as score_eval


def _scored(url, keep, tags):
    return {"url": url, "title": "t", "text": "x", "keep": keep, "tags": tags, "reasoning": "r"}


def test_all_correct_gives_100_percent_accuracy():
    labeled_set = [
        {"item_id": "https://a", "correct_decision": "keep", "correct_tags": ["evals"], "notes": ""},
        {"item_id": "https://b", "correct_decision": "drop", "correct_tags": [], "notes": ""},
    ]
    source_by_url = {
        "https://a": {"url": "https://a", "title": "A", "text": "a"},
        "https://b": {"url": "https://b", "title": "B", "text": "b"},
    }
    rescored = [_scored("https://a", True, ["evals"]), _scored("https://b", False, [])]

    with patch.object(score_eval, "_score_batch", return_value=(rescored, 100, 20)):
        report = score_eval.run_eval(labeled_set, source_by_url)

    assert report["n_items"] == 2
    assert report["keep_accuracy"] == 1.0
    assert report["mean_tag_overlap"] == 1.0
    assert report["input_tokens"] == 100
    assert report["output_tokens"] == 20


def test_partial_mismatch_gives_real_fractional_accuracy():
    labeled_set = [
        {"item_id": "https://a", "correct_decision": "keep", "correct_tags": ["evals"], "notes": ""},
        {"item_id": "https://b", "correct_decision": "keep", "correct_tags": ["memory-systems"], "notes": ""},
    ]
    source_by_url = {
        "https://a": {"url": "https://a", "title": "A", "text": "a"},
        "https://b": {"url": "https://b", "title": "B", "text": "b"},
    }
    # score_node actually dropped "b" (mismatch) and tagged "a" with an extra tag (partial overlap)
    rescored = [_scored("https://a", True, ["evals", "llm-tooling"]), _scored("https://b", False, [])]

    with patch.object(score_eval, "_score_batch", return_value=(rescored, 100, 20)):
        report = score_eval.run_eval(labeled_set, source_by_url)

    assert report["keep_accuracy"] == 0.5  # 1 of 2 keep/drop decisions matched
    # "a" fully overlaps (1.0), "b" was dropped so has zero actual tags against
    # its non-empty expected_tags (0.0) -- mean of the two is 0.5
    assert report["mean_tag_overlap"] == 0.5
    a_result = next(r for r in report["results"] if r["item_id"] == "https://a")
    b_result = next(r for r in report["results"] if r["item_id"] == "https://b")
    assert a_result["keep_match"] is True
    assert b_result["keep_match"] is False


def test_missing_item_id_raises_instead_of_silently_skipping():
    labeled_set = [{"item_id": "https://missing", "correct_decision": "keep", "correct_tags": [], "notes": ""}]
    source_by_url = {}

    try:
        score_eval.run_eval(labeled_set, source_by_url)
        assert False, "expected ValueError for a labeled item_id missing from source data"
    except ValueError as e:
        assert "https://missing" in str(e)
