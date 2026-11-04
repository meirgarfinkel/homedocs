"""Stand-ins for the models, with the same methods: quick, no downloads, and predictable."""

import re
import zlib

import numpy as np


def _words(text):
    return re.findall(r"\w+", text.lower())


class FakeEmbedder:
    """Each word hashed into one of 64 numbers: texts sharing words get similar vectors."""

    key = "fake-embedder"
    dim = 64

    def embed(self, texts, batch=8):
        out = np.zeros((len(texts), self.dim), np.float32)
        for i, t in enumerate(texts):
            for w in _words(t):
                out[i, zlib.crc32(w.encode()) % self.dim] += 1
        out[:, 0] += 1e-3  # (no zero vectors)
        return out / np.linalg.norm(out, axis=1, keepdims=True)

    def embed_question(self, question):
        return self.embed([question])[0]


class FakeReranker:
    """Scores a passage by how many of the question's words it has."""

    def score(self, question, passages):
        q = set(_words(question))
        return [float(len(q & set(_words(p)))) for p in passages]
