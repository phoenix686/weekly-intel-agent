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

from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"
COST_PER_TOKEN_USD = 0.0  # local compute, not a billed API call

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> tuple[list[list[float]], int]:
    """Embeds a batch of texts locally. Returns (vectors, total_tokens) --
    total_tokens is real (summed from the model's own attention_mask,
    not padded batch width), used for NodeCost.input_tokens/observability;
    cost_usd is always 0.0 regardless, since this is local compute.

    Raises whatever sentence-transformers/torch raises on failure --
    callers are responsible for the graceful-degradation handling this
    project's spec requires (skip the pre-filter for that item, don't
    drop it), not this function."""
    model = _get_model()
    vectors = model.encode(texts, convert_to_numpy=True).tolist()
    encoded = model.preprocess(texts)
    total_tokens = int(encoded["attention_mask"].sum().item())
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
