# Eval engineering

Fixtures are immutable JSONL datasets with `seed`, `regression`, and `holdout`
splits. `validate_datasets.py` is the offline PR gate; `bootstrap_datasets.py`
uploads a versioned copy to the `weekly-intel-evals` LangSmith project.

Deterministic gates cover schema, URLs, freshness, 3,800-character Telegram
limits, state transitions, exactly-once behavior, duplicate leakage,
Precision@10, nDCG@10, and must-read recall. Subjective usefulness is annotated
in LangSmith and judged with randomized order, three repetitions, and a
human-calibrated Anthropic rubric. Production errors, fallbacks, duplicate
reports, negative feedback, and 10% of healthy runs feed an annotation queue;
human approval is required before promotion to a release gate.

The six stateful failure tasks are documented in `harbor/tasks.json`; each is
also exercised by the repository's failure-injection tests.
