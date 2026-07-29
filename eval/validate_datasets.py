"""Offline release-gate validation for versioned eval fixtures."""
from dataset_seeds import DATASETS

REQUIRED = {
    "intel-dedup-v1.jsonl": 20, "intel-ranking-v1.jsonl": 30,
    "intel-feedback-v1.jsonl": 20, "intel-digest-quality-v1.jsonl": 15,
    "intel-preference-learning-v1.jsonl": 15, "intel-saturday-v1.jsonl": 6,
}

for filename, minimum in REQUIRED.items():
    name = filename.removesuffix(".jsonl")
    rows = DATASETS[name]
    assert len(rows) >= minimum, f"{name}: expected >= {minimum}, got {len(rows)}"
    assert {row["split"] for row in rows} <= {"seed", "regression", "holdout"}
print("eval datasets valid")
