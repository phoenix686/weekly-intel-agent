"""
Shared Groq (openai/gpt-oss-120b) client for the three production nodes
switched over from Anthropic/Haiku on 2026-07-26 (score_node,
correlate_trello, classify_item) -- see scripts/compare_groq_harness.py
for the comparison run that preceded this swap.

Lazy singleton, not a module-level `Groq(...)` call: unlike
anthropic.Anthropic() (which tolerates a missing API key at construction
time and only fails on the real call), Groq() raises GroqError
immediately if GROQ_API_KEY is unset anywhere it can find it. A
module-level client in a node file would make `import
saturday.nodes.classify_item` itself fail under plain `pytest tests/`
(no .env loaded there by design -- see tests/conftest.py's header) even
though every test that imports it mocks the call, never needs a real
key. get_groq_client() defers construction to first real call, same
lazy-getter pattern already used for the store (see
saturday/memory_store_config.py's get_store()).

Timeout/retry: no persisted evidence in this repo of the specific 39.5s
classify_item outlier mentioned when this swap was requested (checked
data/cost_log.csv, core/observability.py's node_summary namespace, and
every data/test_results_*.json -- none capture per-attempt Groq harness
latency; scripts/compare_groq_harness.py only prints to stdout, nothing
persisted from that run). Root cause is therefore inferred, not
confirmed: most likely Groq shared-cluster tail latency under
schema-validation overhead, plausibly worse for classify_item since its
prompt (full trello_cards + correlated_items block) is one of the
larger/more reasoning-heavy of the three. Regardless of cause, a call
must not be allowed to run unbounded: GROQ_TIMEOUT is set deliberately
ABOVE the observed 39.5s (so a legitimately-slow-but-real completion
isn't killed and wastefully retried) while still bounding worst-case
per-attempt latency to something trivial against the Saturday job's
45-minute budget (.github/workflows/saturday.yml). max_retries relies on
the Groq SDK's own built-in exponential-backoff retry (confirmed in
groq/_base_client.py: httpx.TimeoutException and connection errors are
retried automatically, same as 429/5xx) rather than a hand-rolled loop.
"""

import os

import httpx
from groq import Groq

GROQ_MODEL = "openai/gpt-oss-120b"

# Real published pricing for openai/gpt-oss-120b, https://groq.com/pricing
# (checked 2026-07-26 -- same rate already verified against a real
# comparison run in scripts/compare_groq_harness.py).
GROQ_COST_PER_INPUT_TOKEN = 0.15 / 1_000_000
GROQ_COST_PER_OUTPUT_TOKEN = 0.60 / 1_000_000

# 45s per attempt: above the observed 39.5s outlier (don't kill a real,
# if slow, completion), well below anything that could jeopardize the
# 45-minute Saturday job budget or the approval-gate checkpoint even
# across every retry attempt.
GROQ_TIMEOUT = httpx.Timeout(45.0, connect=5.0)
GROQ_MAX_RETRIES = 2

_client: Groq | None = None


def get_groq_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(
            api_key=os.environ["GROQ_API_KEY"],
            timeout=GROQ_TIMEOUT,
            max_retries=GROQ_MAX_RETRIES,
        )
    return _client


def groq_cost(input_tokens: int, output_tokens: int) -> float:
    return round(
        input_tokens * GROQ_COST_PER_INPUT_TOKEN + output_tokens * GROQ_COST_PER_OUTPUT_TOKEN,
        6,
    )
