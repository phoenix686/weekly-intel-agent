"""Versioned bootstrap ground truth. Human edits, never generated labels."""

USER_FEEDBACK = [
    (1, 3, ["security", "sandbox"], None), (2, 1, [], None),
    (3, 3, ["computer-use-agents"], None), (4, 0, [], 1),
    (5, 2, ["infrastructure"], None), (6, 1, ["release"], None),
    (7, 2, ["a2a"], None), (8, 2, ["opencode"], None),
    (9, 1, [], None), (10, 0, ["redundant_coverage"], None),
    (11, 3, ["a-b-routing"], None), (12, 1, [], None),
    (13, 1, [], None), (14, 1, [], None), (15, 0, [], 14),
]

DATASETS = {
    "intel-dedup-v1": [
        {"inputs": {"left": f"story-{i//2}", "right": f"story-{i//2 if i % 3 == 0 else i}"},
         "outputs": {"class": "same_story" if i % 3 == 0 else
                     ("related_development" if i % 3 == 1 else "unrelated")},
         "split": ("holdout" if i >= 16 else "regression" if i >= 10 else "seed")}
        for i in range(20)
    ],
    "intel-ranking-v1": [
        {"inputs": {"story_id": f"rank-{i}", "preference_version": 1},
         "outputs": {"relevance": USER_FEEDBACK[i][1] if i < 15 else i % 4,
                     "must_include": i in {0, 2, 10}, "must_exclude": i in {3, 14}},
         "split": ("holdout" if i >= 24 else "regression" if i >= 15 else "seed")}
        for i in range(30)
    ],
    "intel-feedback-v1": [
        {"inputs": {"reply": f"{n}. supplied natural feedback example {i}", "item_count": 15},
         "outputs": {"item_number": n, "relevance": relevance, "topics": topics,
                     "duplicate_of": duplicate},
         "split": ("holdout" if i >= 16 else "regression" if i >= 10 else "seed")}
        for i, (n, relevance, topics, duplicate) in enumerate(
            USER_FEEDBACK + [(1, 3, ["security"], None)] * 5
        )
    ],
    "intel-digest-quality-v1": [
        {"inputs": {"digest_id": f"digest-{i}", "story_ids": [f"s{i}", f"s{i+1}"]},
         "outputs": {"duplicate_leakage": i in {3, 10}, "links_valid": True,
                     "within_length": True, "fresh": i != 12},
         "split": ("holdout" if i >= 12 else "regression" if i >= 8 else "seed")}
        for i in range(15)
    ],
    "intel-preference-learning-v1": [
        {"inputs": {"before": {"version": 1}, "event_id": f"event-{i}", "relevance": i % 4},
         "outputs": {"version_increment": 1, "exactly_once": True,
                     "unrelated_fields_stable": True},
         "split": ("holdout" if i >= 12 else "regression" if i >= 8 else "seed")}
        for i in range(15)
    ],
    "intel-saturday-v1": [
        {"inputs": {"case": case}, "outputs": {"exactly_once": True},
         "split": ("holdout" if i == 5 else "regression" if i >= 3 else "seed")}
        for i, case in enumerate(
            ["selection", "carry-forward", "approval", "consolidation", "interruption", "resume"]
        )
    ],
}
