"""The models: which ones (pinned to exact files), downloading them once, and running them.

Each model is a folder in data_dir()/models holding its files. A download goes to a .part file that picks up
where it stopped, is checked against its sha256, and only then gets its real name, so a file with its real
name is always whole.

Embedder and Reranker are the small interfaces search and indexing use (tests swap in fakes with the same
methods): embed(texts) -> unit-length vectors; score(question, passages) -> a relevance score each. The chat
model is run by llm.py (load_chat() below).
"""

import hashlib
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import RERANK_MAX_TOKENS, data_dir

HF = "https://huggingface.co"


@dataclass(frozen=True)
class ModelFile:
    name: str  # its name in the model's folder
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Model:
    key: str  # its folder in models/, and what vectors made by it are marked with
    title: str
    licence: str
    files: tuple[ModelFile, ...]

    @property
    def folder(self) -> Path:
        return data_dir() / "models" / self.key

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    def ready(self) -> bool:
        return all((self.folder / f.name).exists() for f in self.files)


_BGE = f"{HF}/BAAI/bge-small-en-v1.5/resolve/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
_MARCO = f"{HF}/cross-encoder/ms-marco-MiniLM-L6-v2/resolve/233902d25c440f23af6f7d6e94d2946bac0bee0a"
_VOCAB_SHA = "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3"  # (the same file in both)

EMBEDDER = Model(
    "bge-small-en-v1.5",
    "bge-small-en-v1.5 (BAAI)",
    "MIT",
    (
        ModelFile(
            "model.onnx",
            f"{_BGE}/onnx/model.onnx",
            "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35",
            133093490,
        ),
        ModelFile("vocab.txt", f"{_BGE}/vocab.txt", _VOCAB_SHA, 231508),
    ),
)
# The 8-bit version: about 40% faster than full precision, which re-ranking needs to stay well under a second
# on a laptop (it's the same file for Intel and ARM).
RERANKER = Model(
    "ms-marco-MiniLM-L6-v2-int8",
    "ms-marco-MiniLM-L6-v2 (sentence-transformers), 8-bit",
    "Apache 2.0",
    (
        ModelFile(
            "model.onnx",
            f"{_MARCO}/onnx/model_qint8_avx512_vnni.onnx",
            "3573b6b9593cb2f75987a31815d409ca3dd8808629118fd20451bb1a5d90cec7",
            23200716,
        ),
        ModelFile("vocab.txt", f"{_MARCO}/vocab.txt", _VOCAB_SHA, 231508),
    ),
)
MODELS = (EMBEDDER, RERANKER)

# Qwen3-1.7B, 4-bit (Q4_K_M), as converted by Unsloth (Qwen publishes only an 8-bit file of the small Qwen3s).
# Chosen by the test in REQUIREMENTS.md: the only model tried that got all 20 questions right.
_QWEN3 = f"{HF}/unsloth/Qwen3-1.7B-GGUF/resolve/d7f544eead698dbd1f15126ef60b45a1e1933222"
CHAT = Model(
    "qwen3-1.7b-q4km",
    "Qwen3-1.7B (Qwen), 4-bit",
    "Apache 2.0",
    (
        ModelFile(
            "model.gguf",
            f"{_QWEN3}/Qwen3-1.7B-Q4_K_M.gguf",
            "b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897",
            1107409472,
        ),
    ),
)

# bge-small wants questions (not passages) to start with this
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class DownloadError(OSError):
    """A model couldn't be downloaded (no connection, or it came through damaged)."""


def download(models=MODELS, progress=lambda done, total: None, should_stop=lambda: False) -> bool:
    """Download whatever's missing of models. progress(bytes done, bytes in all). False if stopped; raises
    DownloadError."""
    files = [(m, f) for m in models for f in m.files]
    total = sum(f.size for _, f in files)
    done = sum(f.size for m, f in files if (m.folder / f.name).exists())
    progress(done, total)
    for m, f in files:
        dest = m.folder / f.name
        if dest.exists():
            continue
        m.folder.mkdir(parents=True, exist_ok=True)
        start = done

        def step(got, start=start):
            progress(start + got, total)

        try:
            if not _fetch(f, dest, step, should_stop):
                return False
        except OSError as e:
            raise DownloadError(f"Couldn't download {m.title}: {e}") from e
        done = start + f.size
    return True


def _fetch(f: ModelFile, dest: Path, progress, should_stop) -> bool:
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > f.size:
        part.unlink()
        have = 0
    headers = {"User-Agent": "homedocs"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(f.url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        if have and getattr(r, "status", 200) != 206:  # the server sent all of it: start over
            have = 0
        with open(part, "ab" if have else "wb") as out:
            got = have
            while chunk := r.read(1 << 20):
                if should_stop():
                    return False  # (the .part file stays: the next try carries on from it)
                out.write(chunk)
                got += len(chunk)
                progress(got)
    sha = hashlib.sha256()
    with open(part, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            sha.update(block)
    if part.stat().st_size != f.size or sha.hexdigest() != f.sha256:
        part.unlink()
        raise OSError("it came through damaged. Please try again.")
    os.replace(part, dest)
    return True


# --- running them ----------------------------------------------------------------------------------


def _session(path: Path, threads: int):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, threads)
    opts.inter_op_num_threads = 1
    opts.log_severity_level = 3  # errors only
    return ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])


def _inputs(seqs: list[list[int]], types: list[list[int]] | None = None) -> dict:
    import numpy as np

    width = max(len(s) for s in seqs)
    ids = np.zeros((len(seqs), width), np.int64)
    mask = np.zeros_like(ids)
    tt = np.zeros_like(ids)
    for i, s in enumerate(seqs):
        ids[i, : len(s)] = s
        mask[i, : len(s)] = 1
        if types:
            tt[i, : len(types[i])] = types[i]
    return {"input_ids": ids, "attention_mask": mask, "token_type_ids": tt}


def threads(share: float = 1.0) -> int:
    """A share of the computer's cores (indexing uses half, so the computer stays quick)."""
    return max(1, int((os.cpu_count() or 2) * share))


class Embedder:
    """bge-small-en-v1.5: text -> a 384-number unit vector (its [CLS] output), similar meaning, similar
    vectors."""

    dim = 384

    def __init__(self, model: Model = EMBEDDER, cores: int | None = None):
        from .tokenizer import Tokenizer

        self.key = model.key
        self.tok = Tokenizer(model.folder / "vocab.txt")
        self.session = _session(model.folder / "model.onnx", cores or threads(0.5))

    def embed(self, texts: list[str], batch: int = 8):
        import numpy as np

        seqs = [self.tok.encode(t) for t in texts]
        out = np.zeros((len(texts), self.dim), np.float32)
        order = sorted(range(len(seqs)), key=lambda i: len(seqs[i]))  # alike lengths together: less padding
        for at in range(0, len(order), batch):
            idx = order[at : at + batch]
            hidden = self.session.run(None, _inputs([seqs[i] for i in idx]))[0]
            out[idx] = hidden[:, 0]
        out /= np.linalg.norm(out, axis=1, keepdims=True).clip(1e-12)
        return out

    def embed_question(self, question: str):
        return self.embed([QUERY_PREFIX + question])[0]


class Reranker:
    """ms-marco-MiniLM-L6-v2: reads the question and a passage together and scores how well the passage
    answers it (higher is better). Slower than comparing vectors, much better at telling which is best,
    so it only sees the few best candidates, each cut to RERANK_MAX_TOKENS."""

    def __init__(self, model: Model = RERANKER, cores: int | None = None):
        from .tokenizer import Tokenizer

        self.tok = Tokenizer(model.folder / "vocab.txt")
        self.session = _session(model.folder / "model.onnx", cores or threads(0.5))

    def score(self, question: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        pairs = [self.tok.encode_pair(question, p, RERANK_MAX_TOKENS) for p in passages]
        logits = self.session.run(None, _inputs([p[0] for p in pairs], [p[1] for p in pairs]))[0]
        return [float(x) for x in logits.ravel()]


def load_chat(model: Model = CHAT):
    """The chat model, loaded by llama.cpp (llm.LLMError if it can't be)."""
    from .config import N_CTX
    from .llm import LLM

    return LLM(model.folder / model.files[0].name, N_CTX, threads(0.5))
