"""
NVIDIA NIM embedding wrapper (nvidia/nemotron-3-embed-1b, hosted via
build.nvidia.com's OpenAI-compatible /v1/embeddings endpoint). Replaces
the prior local sentence-transformers provider (all-MiniLM-L6-v2) --
2026-07-19. History, stated plainly so this doesn't get re-litigated:
Voyage AI (original) -> gemini-embedding-001 (abandoned, unresolved
429/API_KEY_INVALID after a multi-hour live debugging session) -> local
sentence-transformers (final choice at the time, no key/account/billing
tier) -> this NVIDIA swap.

Real, live-verified 2026-07-19 against the actual endpoint (not assumed):
- Model: nvidia/nemotron-3-embed-1b, confirmed present in the real
  /v1/models catalog for this account.
- Output dimension: 2048 -- DIFFERENT from the prior local model's 384.
  This is a breaking change for anything comparing a vector embedded
  under the old provider against one embedded under this one (see
  EMBEDDING_DIM and cosine_similarity's dimension-mismatch guard below).
  Both weekly_intel store namespaces holding old 384-dim vectors
  (recent_item_embeddings, taste_topic_vectors) were cleared as part of
  this swap -- both are fully derivable/recomputable state, not
  source-of-truth data (recent_item_embeddings rebuilds itself over the
  next _WINDOW_DAYS of real runs; taste_topic_vectors' consumer,
  taste_prefilter(), already has a permissive empty-store fallback that
  lets everything through rather than dropping everything, so an empty
  store degrades safely rather than corrupting comparisons).
- Batching: confirmed real, one API call for up to 50 texts (this
  project's existing BATCH_SIZE convention), ~1.4s, all vectors returned
  at consistent dimension. The response's per-item "index" field is used
  to place each vector, not raw array order -- the API's own documented
  contract, not guaranteed array-order-preserving even though it was
  observed sequential in testing.
- input_type: NVIDIA's asymmetric embedding models produce MEASURABLY
  DIFFERENT vectors for input_type="query" vs "passage" on identical
  text (live-tested: cosine similarity only ~0.85 between the two for
  the same string, not ~1.0) -- this is a retrieval-style
  query/passage-optimized model, not a single-space general embedder
  like the old local model. This module standardizes on "passage" for
  EVERY call (both semantic_dedup.py's item-vs-item comparisons and
  taste_vectors.py's topic-vs-item comparisons), to preserve the same
  "everything lives in one comparable space" symmetric behavior the old
  model had. NVIDIA's docs on the query/passage split for THIS specific
  model could not be fetched live (two attempts at build.nvidia.com
  timed out) -- if that convention turns out to matter for match
  quality, taste_vectors.py's topic-vs-item comparison is the more
  likely candidate to revisit first (topic description ~ query, item ~
  passage is the closer fit to typical retrieval framing).
- Pricing: UNVERIFIED. Both live fetch attempts against NVIDIA's
  pricing docs timed out, and the real API response carries no
  cost/credit/billing header of any kind (checked all response headers
  directly). COST_PER_TOKEN_USD is therefore NOT a confirmed rate the
  way the old local-compute $0.0 was categorically true -- it's a
  placeholder. NodeCost.cost_usd figures downstream of this module will
  under-report real spend if build.nvidia.com's embeddings endpoint
  turns out to be billed. Flagged here and in the swap's own commit/
  report, not silently assumed free.
- Per-item token counts: the API returns one AGGREGATE usage.total_tokens
  per batch call, not real per-item counts the way the old model's
  attention_mask gave (exactly, per text). For a single-text call the
  aggregate IS the real per-item count (no approximation needed). For a
  multi-text batch, per-item counts are approximated by each text's
  share of total character length across the batch -- an approximation,
  not measured per-item data, documented here so it's never mistaken for
  the old exact accounting.

Shared by semantic dedup (discovery/semantic_dedup.py), the
taste-similarity pre-filter (discovery/taste_vectors.py), and topic-vector
recompute (discovery/taste_vectors.recompute_topic_vectors, called from
sunday/nodes/update_profile.py's Sunday consolidated rewrite). Interface
(embed_text, embed_texts, cosine_similarity, COST_PER_TOKEN_USD)
unchanged from the local-model version -- confirmed isolated swap, same
as the prior provider transitions this module has already been through.

No langgraph imports.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

from sunday.memory_store_config import get_store

logger = logging.getLogger(__name__)

MODEL_NAME = "nvidia/nemotron-3-embed-1b"
EMBEDDING_DIM = 2048  # live-verified 2026-07-19; see module docstring
NVIDIA_API_BASE = "https://integrate.api.nvidia.com/v1/embeddings"
INPUT_TYPE = "passage"  # see module docstring -- standardized for symmetric comparison

# UNVERIFIED -- see module docstring. Not a confirmed free rate.
COST_PER_TOKEN_USD = 0.0

# NVIDIA's /v1/embeddings endpoint hard-caps input at 65,536 characters per
# text (confirmed live, 2026-07-23: a real 76,675-char MarkTechPost article
# 400'd the whole batch call). Shared by every caller that batch-embeds
# real scraped article text -- discovery/semantic_dedup.py (fixed
# 2026-07-23) and discovery/taste_vectors.py (fixed 2026-07-25, after the
# same bug was found silently disabling the taste pre-filter on any day a
# batch happened to include an oversized article: the failure was being
# caught and treated as "let everything through unfiltered," which is how
# two apparently-good days turned out to be the filter never having run at
# all). 8000 chars is far under the real cap with headroom for a full
# batch, and is plenty of signal for a similarity comparison -- a
# 76K-char article doesn't need to be embedded in full to detect topical
# fit. One shared constant, not two copies of the same magic number.
MAX_EMBED_CHARS = 8000

_FAILURES_NAMESPACE = ("weekly_intel", "embedding_failures")


def record_embedding_failure(module: str, run_id: str, item_count: int, error: str) -> None:
    """Durable record of a batch embed failure, shared by every caller of
    embed_texts()/embed_text() in this project. `module` identifies which
    caller hit it (e.g. "semantic_dedup", "taste_prefilter") -- this is
    the second real call site to need this after semantic_dedup.py's
    2026-07-23 fix, so the schema now records which one failed instead of
    silently assuming there's only ever one. A failed write here must
    never block the graceful-degradation path it's describing, same
    reliability requirement as every other observability write in this
    project (approval_log, node_summary)."""
    try:
        get_store().put(
            _FAILURES_NAMESPACE,
            str(uuid.uuid4()),
            {
                "module": module,
                "run_id": run_id,
                "item_count": item_count,
                "error": error,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as e:
        logger.warning(f"embeddings: embedding_failures write itself failed (module={module}, run={run_id}): {e}")


def _api_key() -> str:
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise KeyError("NVIDIA_API_KEY is not set in the environment")
    return key


def _embeddings_request(texts: list[str]) -> dict:
    payload = {"input": texts, "model": MODEL_NAME, "input_type": INPUT_TYPE}
    req = urllib.request.Request(
        NVIDIA_API_BASE,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def embed_texts(texts: list[str]) -> tuple[list[list[float]], list[int]]:
    """Embeds a batch of texts in ONE real API call -- callers with
    multiple texts to embed (discovery/semantic_dedup.py,
    discovery/taste_vectors.py) should call this once with the full list
    rather than looping embed_text() per item, same batching requirement
    as every other batched call in this project.

    Returns (vectors, per_item_tokens). per_item_tokens is REAL for a
    single-text call (the API's aggregate usage.total_tokens IS that
    text's count); for a multi-text batch it's an approximation --
    total_tokens distributed proportionally by each text's character
    length, since the API only reports one aggregate count per call, not
    real per-item counts. See module docstring.

    Raises whatever urllib/json raises on failure -- callers are
    responsible for the graceful-degradation handling this project's
    spec requires (skip the pre-filter for that item, don't drop it),
    not this function."""
    if not texts:
        return [], []

    logger.debug(f"embeddings: BEFORE NVIDIA embeddings request ({len(texts)} text(s))")
    t0 = time.perf_counter()
    body = _embeddings_request(texts)
    logger.debug(f"embeddings: AFTER NVIDIA embeddings request ({time.perf_counter() - t0:.3f}s)")

    vectors_by_index = {entry["index"]: entry["embedding"] for entry in body["data"]}
    vectors = [vectors_by_index[i] for i in range(len(texts))]

    total_tokens = body["usage"]["total_tokens"]
    if len(texts) == 1:
        per_item_tokens = [total_tokens]
    else:
        total_chars = sum(len(t) for t in texts) or 1
        per_item_tokens = [round(total_tokens * len(t) / total_chars) for t in texts]

    return vectors, per_item_tokens


def embed_text(text: str) -> tuple[list[float], int]:
    """Single-text convenience wrapper over embed_texts. Callers embedding
    MULTIPLE texts should call embed_texts() directly instead of looping
    this -- see embed_texts' docstring. The returned token count is real
    (not approximated) for this single-text case."""
    vectors, tokens = embed_texts([text])
    return vectors[0], tokens[0]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Standard cosine similarity. Returns 0.0 for a zero vector (rather
    than raising ZeroDivisionError) -- a degenerate embedding shouldn't
    crash a comparison loop.

    Also returns 0.0 on a dimension mismatch (added with the NVIDIA swap,
    2026-07-19) -- zip() silently truncates to the shorter vector's
    length on a length mismatch, which would otherwise compute a
    meaningless partial-dimension "similarity" with no error at all.
    Treating a mismatch as "not a match" (same value already used for
    the degenerate-vector case) is the safe default: it can only ever
    make an item look LESS similar than it might really be, never more,
    matching this pre-filter's own stated bias (a false negative here is
    worse than a false positive, but a silently corrupted score is worse
    than either)."""
    if len(a) != len(b):
        logger.warning(f"cosine_similarity: dimension mismatch ({len(a)} vs {len(b)}) -- treating as no match, not truncating")
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
