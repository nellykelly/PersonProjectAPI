"""Text embedders.

Production uses `fastembed` (ONNX, no torch) with BAAI/bge-small-en-v1.5,
384-dimensional. The test suite uses `HashEmbedder` -- deterministic,
dependency-free, no model download -- which is enough to check that
retrieval orders chunks sensibly.

Both return plain `list[float]`. The fastembed instance is cached because
constructing it loads a model off disk.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from .errors import AssistantUnavailable

_WORD_RE = re.compile(r"[A-Za-z0-9]+")


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_one(self, text: str) -> list[float]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _l2_normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class HashEmbedder:
    """A hashing bag-of-features embedder: each word and each character
    trigram is hashed into a bucket and its weight accumulated, then the
    vector is L2-normalised. Not semantic, but deterministic and free --
    similar text lands near similar text, which is all the tests need."""

    def __init__(self, dim: int = 384):
        self.dim = dim

    def _feature_bucket(self, token: str) -> int:
        h = hashlib.md5(token.encode("utf-8")).digest()
        return int.from_bytes(h[:4], "big") % self.dim

    def embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        words = _WORD_RE.findall(text.lower())
        for w in words:
            vec[self._feature_bucket("w:" + w)] += 1.0
            for i in range(len(w) - 2):
                vec[self._feature_bucket("t:" + w[i : i + 3])] += 0.5
        return _l2_normalise(vec)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]

    # No query/passage asymmetry for a hashing embedder.
    def embed_query(self, text: str) -> list[float]:
        return self.embed_one(text)


# BGE-family models are trained asymmetrically: passages are embedded as-is,
# but a *query* is meant to carry this instruction prefix. Skipping it
# noticeably flattens similarity scores on short questions.
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class FastEmbedEmbedder:
    def __init__(self, model_name: str, dim: int):
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - prod has it
            raise AssistantUnavailable(
                "fastembed is not installed -- the assistant cannot embed content"
            ) from exc
        self._model = TextEmbedding(model_name=model_name)
        self._model_name = model_name or ""
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in v] for v in self._model.embed(list(texts))]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def embed_query(self, text: str) -> list[float]:
        if "bge" in self._model_name.lower():
            text = _BGE_QUERY_PREFIX + text
        return self.embed([text])[0]


_CACHE: dict[tuple, Embedder] = {}


def build_embedder(config) -> Embedder:
    kind = config["ASSISTANT_EMBEDDER"]
    dim = int(config["ASSISTANT_EMBED_DIM"])
    model = config["ASSISTANT_EMBED_MODEL"]
    key = (kind, model, dim)
    if key in _CACHE:
        return _CACHE[key]

    if kind == "hash":
        emb: Embedder = HashEmbedder(dim)
    elif kind == "fastembed":
        emb = FastEmbedEmbedder(model, dim)
    else:
        raise AssistantUnavailable(f"unknown ASSISTANT_EMBEDDER {kind!r}")

    _CACHE[key] = emb
    return emb
