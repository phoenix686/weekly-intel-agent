"""
Gemini embedding client wrapper (gemini-embedding-001 via Google AI
Studio), per batch2-dedup-taste-spec.md Section 3. Shared by semantic
dedup (discovery/semantic_dedup.py), the taste-similarity pre-filter
(discovery/taste_vectors.py), and the Sunday consolidated taste-profile
rewrite's topic-vector recompute (sunday/nodes/update_profile.py, via
discovery/taste_vectors.recompute_topic_vectors).

Requires GEMINI_API_KEY in the environment -- not committed, added as a
GitHub Secret separately (see CLAUDE.md Section 8).

COST_PER_TOKEN_USD is 0.0: Google AI Studio's free tier (10M
tokens/minute, recurring, not a depleting cap -- see spec Section 3) has
no per-token charge at this project's volume, as long as billing stays
off the backing Google Cloud project. This is a deliberate reflection of
real pricing, not a placeholder.

Response shape verified directly against the installed google-genai SDK
(2.12.0) source, not guessed: EmbedContentResponse.embeddings is a
list[ContentEmbedding], each with .values (list[float]). Per-call token
counts are genuinely unavailable here -- ContentEmbeddingStatistics.
token_count is documented in the SDK itself as "Gemini Enterprise Agent
Platform only", not populated on the standard AI Studio API this project
uses -- so embed_texts returns 0 for total_tokens, a confirmed fact about
the API surface, not an unverified guess pending a live key.

No langgraph imports.
"""

from __future__ import annotations

import os

from google import genai
from google.genai import types

MODEL = "gemini-embedding-001"
COST_PER_TOKEN_USD = 0.0  # Google AI Studio free tier -- see module docstring

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _client


def embed_texts(texts: list[str]) -> tuple[list[list[float]], int]:
    """Embeds a batch of texts. Returns (vectors, total_tokens) -- caller
    uses total_tokens * COST_PER_TOKEN_USD for NodeCost.cost_usd (always
    0.0 on the free tier; total_tokens is always 0, see module docstring
    -- kept as a field for NodeCost shape consistency with the rest of
    the codebase, not because it carries real data here).

    task_type=SEMANTIC_SIMILARITY -- every caller of this module compares
    embeddings against each other via cosine similarity (dedup,
    taste-matching), the symmetric use case that task type is documented
    for, as opposed to asymmetric retrieval (query vs. document).

    Raises whatever the genai SDK raises on failure (auth error, network
    error, etc.) -- callers are responsible for the graceful-degradation
    handling this project's spec requires (skip the pre-filter for that
    item, don't drop it), not this function."""
    result = _get_client().models.embed_content(
        model=MODEL,
        contents=texts,
        config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY"),
    )
    vectors = [e.values for e in result.embeddings]
    total_tokens = 0
    return vectors, total_tokens


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
