"""Embedding engine for vxdb, wrapping fastembed."""

from fastembed import TextEmbedding


class Embedder:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        self._model = TextEmbedding(model_name=model_name)
        probe = next(self._model.embed(["probe"]))
        self._dimension = len(probe)

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> list[float]:
        return next(self._model.embed([text])).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]
