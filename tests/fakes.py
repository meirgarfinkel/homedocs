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


class FakeLLM:
    """Answers with the first sentence of passage [1], a word at a time ("Not in your files." with no
    passages). delay: seconds per word, to test stopping. Same methods as llm.LLM."""

    def __init__(self, delay=0.0):
        self.delay = delay
        self.prompts = []  # (instructions, rest) of every answer asked for
        self._stop = False

    def generate(self, instructions, rest, max_new, should_stop=lambda: False):
        import time

        from homedocs.core.llm import Stopped

        self._stop = False
        self.prompts.append((instructions, rest))
        m = re.search(r"\[1\] [^\n]*\n(.*?)(?:\n\n\[2\]|\n\nQuestion:)", rest, re.S)
        body = "\n".join(line for line in m.group(1).splitlines() if not line.startswith("#")) if m else ""
        answer = re.split(r"(?<=[.!?])\s", body.strip())[0] if body.strip() else "Not in your files."
        for i, word in enumerate(answer.split(" ")):
            if self._stop or should_stop():
                raise Stopped
            time.sleep(self.delay)
            yield word if i == 0 else " " + word

    def stop(self):
        self._stop = True

    def close(self):
        pass
