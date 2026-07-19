"""
discovery/embeddings.py -- cosine_similarity is pure math, provider-
independent, real assertions not mocked. embed_texts/embed_text (NVIDIA
NIM provider, swapped in 2026-07-19) mock urllib.request.urlopen so this
stays fully offline -- no real HTTP call, no real NVIDIA_API_KEY needed
to run these.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import patch, MagicMock

import discovery.embeddings as embeddings_mod
from discovery.embeddings import cosine_similarity, embed_texts, embed_text


def test_identical_vectors_score_one():
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0


def test_orthogonal_vectors_score_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_opposite_vectors_score_negative_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0


def test_zero_vector_scores_zero_not_a_crash():
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine_similarity([1.0, 1.0], [0.0, 0.0]) == 0.0


def test_scaled_same_direction_vectors_score_one():
    assert abs(cosine_similarity([1.0, 1.0], [2.0, 2.0]) - 1.0) < 1e-9


def test_dimension_mismatch_scores_zero_not_silently_truncated():
    """Added with the NVIDIA swap (2026-07-19, 384 -> 2048 dims): zip()
    would otherwise silently compute a meaningless partial-dimension
    similarity on a mismatch instead of erroring or flagging it."""
    long_vector = [1.0] * 2048
    short_vector = [1.0] * 384
    assert cosine_similarity(long_vector, short_vector) == 0.0
    assert cosine_similarity(short_vector, long_vector) == 0.0


def _fake_response(data: list[list[float]], total_tokens: int):
    body = {
        "data": [{"index": i, "embedding": vec} for i, vec in enumerate(data)],
        "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
        "model": "nvidia/nemotron-3-embed-1b",
    }
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_embed_texts_empty_list_returns_empty_without_a_call():
    with patch.object(embeddings_mod.urllib.request, "urlopen") as mock_urlopen:
        vectors, tokens = embed_texts([])
    assert vectors == []
    assert tokens == []
    mock_urlopen.assert_not_called()


def test_embed_texts_single_text_real_not_approximated_token_count():
    with patch.object(embeddings_mod.urllib.request, "urlopen", return_value=_fake_response([[0.1, 0.2]], 7)), \
         patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-fake"}):
        vectors, tokens = embed_texts(["one text"])
    assert vectors == [[0.1, 0.2]]
    assert tokens == [7]  # real, not approximated -- the aggregate IS this one text's count


def test_embed_texts_batch_uses_index_field_for_ordering():
    """Response order deliberately scrambled relative to input order --
    embed_texts must place vectors by the response's own index field,
    not raw array position."""
    body = {
        "data": [
            {"index": 1, "embedding": [2.0]},
            {"index": 0, "embedding": [1.0]},
        ],
        "usage": {"prompt_tokens": 10, "total_tokens": 10},
    }
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False

    with patch.object(embeddings_mod.urllib.request, "urlopen", return_value=resp), \
         patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-fake"}):
        vectors, tokens = embed_texts(["first", "second"])
    assert vectors == [[1.0], [2.0]]


def test_embed_texts_batch_approximates_per_item_tokens_by_char_length():
    with patch.object(embeddings_mod.urllib.request, "urlopen",
                       return_value=_fake_response([[0.1], [0.1]], 100)), \
         patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-fake"}):
        vectors, tokens = embed_texts(["a" * 10, "b" * 30])
    # 10 vs 30 chars -> 40 total -> 25%/75% split of the aggregate 100
    assert tokens == [25, 75]


def test_embed_text_wraps_embed_texts_with_one_item():
    with patch.object(embeddings_mod.urllib.request, "urlopen", return_value=_fake_response([[9.0]], 3)), \
         patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-fake"}):
        vector, tokens = embed_text("solo text")
    assert vector == [9.0]
    assert tokens == 3


def test_missing_api_key_raises_key_error():
    with patch.dict(os.environ, {}, clear=True):
        try:
            embed_texts(["text"])
            assert False, "expected KeyError for missing NVIDIA_API_KEY"
        except KeyError as e:
            assert "NVIDIA_API_KEY" in str(e)


def test_request_uses_passage_input_type_and_real_model_name():
    captured = {}

    def _capture_urlopen(req):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        captured["headers"] = dict(req.headers)
        return _fake_response([[0.1]], 5)

    with patch.object(embeddings_mod.urllib.request, "urlopen", side_effect=_capture_urlopen), \
         patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-fake"}):
        embed_texts(["test"])

    assert captured["payload"]["model"] == "nvidia/nemotron-3-embed-1b"
    assert captured["payload"]["input_type"] == "passage"
    assert captured["payload"]["input"] == ["test"]
    assert captured["headers"]["Authorization"] == "Bearer nvapi-fake"
