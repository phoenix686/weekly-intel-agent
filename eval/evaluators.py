"""Deterministic release-gate evaluators."""
from __future__ import annotations
import math
from urllib.parse import urlsplit


def classification_metrics(expected: list[str], actual: list[str], positive: str) -> dict:
    tp = sum(e == positive and a == positive for e, a in zip(expected, actual))
    fp = sum(e != positive and a == positive for e, a in zip(expected, actual))
    fn = sum(e == positive and a != positive for e, a in zip(expected, actual))
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {"precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}


def ndcg_at_k(relevances: list[int], k: int = 10) -> float:
    def dcg(values):
        return sum((2 ** value - 1) / math.log2(index + 2)
                   for index, value in enumerate(values[:k]))
    ideal = dcg(sorted(relevances, reverse=True))
    return dcg(relevances) / ideal if ideal else 1.0


def digest_checks(text: str, item_map: dict[int, dict]) -> dict:
    story_ids = [item.get("story_id") or item.get("url") for item in item_map.values()]
    urls = [item["url"] for item in item_map.values() if item.get("url")]
    return {
        "within_telegram_limit": len(text) <= 3800,
        "unique_stories": len(story_ids) == len(set(story_ids)),
        "valid_urls": all(urlsplit(url).scheme in {"http", "https"} for url in urls),
        "item_count_ok": len(item_map) <= 10,
    }


def state_transition_ok(before: dict, after: dict, acknowledged: bool) -> bool:
    protected = ("seen_urls", "dedup_urls", "carry_forward_urls")
    return acknowledged or all(before.get(key) == after.get(key) for key in protected)
