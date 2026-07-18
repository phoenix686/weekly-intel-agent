"""
Local sentence-transformers embedding wrapper (all-MiniLM-L6-v2), per
batch2-dedup-taste-spec.md Section 3 -- final provider decision. History,
stated plainly so this doesn't get re-litigated: Voyage AI was the
original choice (replaced for a better free-tier shape), then
gemini-embedding-001 (abandoned after a multi-hour live debugging
session -- confirmed real key, confirmed correct project, confirmed
correct key format -- the very first real API request still returned an
unresolved 429/API_KEY_INVALID class of error). Cohere and HuggingFace's
Inference Providers were both considered next and rejected for the same
underlying risk category (opaque free-tier caps, real reports of
accounts hitting unexpected errors on steady low usage) before landing
here.

Local is the deliberate final choice, not a fallback: no API key, no
account, no billing tier, no credit balance that can silently run out or
misconfigure -- the entire category of problem that cost hours with
Gemini does not exist for a local model. Same model this project's
original reference script used.

Shared by semantic dedup (discovery/semantic_dedup.py), the
taste-similarity pre-filter (discovery/taste_vectors.py), and topic-vector
recompute (discovery/taste_vectors.recompute_topic_vectors, called from
sunday/nodes/update_profile.py's Sunday consolidated rewrite).

No API key, no secret, no environment variable required.

COST_PER_TOKEN_USD is 0.0: local compute, not a billed API call.
total_tokens IS real (via the model's own attention_mask, not padded
length -- confirmed by direct inspection: a 2-text batch with one 5-token
and one 9-token real input pads input_ids to a shared width of 9, but
attention_mask.sum(dim=1) correctly recovers [5, 9], not [9, 9]).

Model produces 384-dimension vectors -- different from both Voyage's and
Gemini's. No downstream code hardcodes a dimension: cosine_similarity
uses zip() over arbitrary-length lists, store schemas hold
embedding_vector as an opaque list[float], all tests mock
embed_text/embed_texts directly -- confirmed isolated swap.

`torch` is sentence-transformers' hard dependency -- requirements.txt
pins the CPU-only build explicitly (this runs in GitHub Actions, no
GPU). Model weights (~80-90MB) download on first use per machine/cache --
see .github/workflows/daily.yml and sunday.yml for the HuggingFace
cache-directory caching this requires in CI, alongside the pip cache.

No langgraph imports.
"""

from __future__ import annotations

import logging
import time

from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"
COST_PER_TOKEN_USD = 0.0  # local compute, not a billed API call

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.debug("embeddings: BEFORE SentenceTransformer(MODEL_NAME) construction (HF_HUB_OFFLINE-gated)")
        t0 = time.perf_counter()
        _model = SentenceTransformer(MODEL_NAME)
        logger.debug(f"embeddings: AFTER SentenceTransformer(MODEL_NAME) construction ({time.perf_counter() - t0:.3f}s)")
    return _model


def embed_texts(texts: list[str]) -> tuple[list[list[float]], list[int]]:
    """Embeds a batch of texts locally in ONE model.encode() call --
    callers with multiple texts to embed (discovery/semantic_dedup.py,
    discovery/taste_vectors.py) should call this once with the full list
    rather than looping embed_text() per item; batching the real forward
    pass measured ~3.4x faster than N single-item calls (150-item local
    benchmark: 3.05s looped vs 0.89s batched).

    Returns (vectors, per_item_tokens) -- per_item_tokens[i] is real,
    from that text's own attention_mask sum (not padded batch width), so
    NodeCost.input_tokens can still be attributed per item even though
    the underlying encode() call is one batch, not one call per item.

    Raises whatever sentence-transformers/torch raises on failure --
    callers are responsible for the graceful-degradation handling this
    project's spec requires (skip the pre-filter for that item, don't
    drop it), not this function."""
    model = _get_model()

    logger.debug(f"embeddings: BEFORE model.encode() ({len(texts)} text(s))")
    t0 = time.perf_counter()
    vectors = model.encode(texts, convert_to_numpy=True).tolist()
    logger.debug(f"embeddings: AFTER model.encode() ({time.perf_counter() - t0:.3f}s)")

    logger.debug(f"embeddings: BEFORE model.preprocess() ({len(texts)} text(s))")
    t0 = time.perf_counter()
    encoded = model.preprocess(texts)
    logger.debug(f"embeddings: AFTER model.preprocess() ({time.perf_counter() - t0:.3f}s)")

    per_item_tokens = encoded["attention_mask"].sum(dim=1).tolist()
    return vectors, per_item_tokens


def embed_text(text: str) -> tuple[list[float], int]:
    """Single-text convenience wrapper over embed_texts. Callers embedding
    MULTIPLE texts should call embed_texts() directly instead of looping
    this -- see embed_texts' docstring."""
    vectors, tokens = embed_texts([text])
    return vectors[0], tokens[0]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Standard cosine similarity. Returns 0.0 for a zero vector (rather
    than raising ZeroDivisionError) -- a degenerate embedding shouldn't
    crash a comparison loop."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
