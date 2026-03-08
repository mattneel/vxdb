"""Tests for vxdb.embedder module."""

import math

import pytest

from vxdb.embedder import Embedder


def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b)


@pytest.fixture(scope="module")
def embedder():
    return Embedder()


class TestEmbedderInit:
    def test_default_model(self, embedder):
        assert embedder._model is not None

    def test_dimension_is_positive_int(self, embedder):
        assert isinstance(embedder.dimension, int)
        assert embedder.dimension > 0

    def test_dimension_is_384(self, embedder):
        assert embedder.dimension == 384


class TestEmbed:
    def test_returns_list_of_floats(self, embedder):
        vec = embedder.embed("hello")
        assert isinstance(vec, list)
        assert all(isinstance(v, float) for v in vec)

    def test_correct_dimension(self, embedder):
        vec = embedder.embed("hello")
        assert len(vec) == embedder.dimension


class TestEmbedBatch:
    def test_returns_correct_count(self, embedder):
        vecs = embedder.embed_batch(["hello", "world"])
        assert len(vecs) == 2

    def test_each_vector_correct_dimension(self, embedder):
        vecs = embedder.embed_batch(["hello", "world"])
        for vec in vecs:
            assert len(vec) == embedder.dimension

    def test_each_vector_is_list_of_floats(self, embedder):
        vecs = embedder.embed_batch(["hello", "world"])
        for vec in vecs:
            assert isinstance(vec, list)
            assert all(isinstance(v, float) for v in vec)


class TestCosineSimilarity:
    def test_similar_texts_closer_than_dissimilar(self, embedder):
        vec_cat = embedder.embed("cat")
        vec_kitten = embedder.embed("kitten")
        vec_database = embedder.embed("database")

        sim_close = cosine_sim(vec_cat, vec_kitten)
        sim_far = cosine_sim(vec_cat, vec_database)

        assert sim_close > sim_far
