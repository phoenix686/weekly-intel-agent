"""
Voyage AI embedding client wrapper (voyage-4-lite), per
batch2-dedup-taste-spec.md Section 9: Anthropic's officially recommended
embeddings partner, $0.02/1M tokens, first 200M tokens free. Shared by
semantic dedup (discovery/semantic_dedup.py), the taste-similarity
pre-filter (discovery/taste_vectors.py), and topic-vector recompute
(sunday/approval_actions.py).

Requires VOYAGE_API_KEY in the environment -- not committed, added as a
GitHub Secret separately (see CLAUDE.md Section 8).

No langgraph imports.
"""

from __future__ import annotations

import os

import voyageai

MODEL = "voyage-4-lite"
COST_PER_TOKEN_USD = 0.02 / 1_000_000  # $0.02 per 1M tokens

_client: voyageai.Client | None = None


def _get_client() -> voyageai.Client:
    global _client
    if _client is None:
        _client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
    return _client


def embed_texts(texts: list[str]) -> tuple[list[list[float]], int]:
    """Embeds a batch of texts. Returns (vectors, total_tokens) -- caller
    uses total_tokens * COST_PER_TOKEN_USD for NodeCost.cost_usd.

    Raises whatever voyageai raises on failure (auth error, network error,
    etc.) -- callers are responsible for the graceful-degradation handling
    this project's spec requires (skip the pre-filter for that item,
    don't drop it), not this function."""
    result = _get_client().embed(texts, model=MODEL, input_type="document")
    return result.embeddings, result.total_tokens


def embed_text(text: str) -> tuple[list[float], int]:
    """Single-text convenience wrapper over embed_texts."""
    vectors, tokens = embed_texts([text])
    return vectors[0], tokens


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
