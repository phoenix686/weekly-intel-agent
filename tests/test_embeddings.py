"""
discovery/embeddings.py's cosine_similarity is pure math, provider-
independent -- real assertions, not mocked, since nothing here touches
the network.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery.embeddings import cosine_similarity


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
