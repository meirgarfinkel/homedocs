"""Our bindings to llama.cpp, which runs the chat model: load it, read a prompt, write the answer a token at a
time, stop at once when asked.

The library is built by packaging/build_llama.sh: llama.cpp plus native/hd_llama.c, which turns the llama.cpp
calls that take settings structs into plain functions, so nothing here depends on a struct's layout. It sits in
the app bundle's `llama` folder, or in build/llama when running from source. Without it, answers are off and
search works as before (available() says which).

The prompt comes in two parts: the instructions, the same for every question, which are read once and kept,
and the rest (the passages and the question), read each time.
"""

import codecs
import ctypes
import os
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

LIB_NAME = {"win32": "hd_llama.dll", "darwin": "libhd_llama.dylib"}.get(sys.platform, "libhd_llama.so")


class LLMError(RuntimeError):
    """The chat model couldn't be loaded or run."""


def lib_dir() -> Path | None:
    """The folder with llama.cpp's libraries: in the app bundle, or build/llama from source; None if not built."""
    here = Path(__file__).resolve().parent
    places = []
    if getattr(sys, "frozen", False):
        places += [Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "llama"]
    places += [here.parent.parent / "build" / "llama"]
    return next((p for p in places if (p / LIB_NAME).exists()), None)


def available() -> bool:
    return lib_dir() is not None


_lib = None
_lib_lock = threading.Lock()


def _library():
    global _lib
    with _lib_lock:
        if _lib is not None:
            return _lib
        folder = lib_dir()
        if folder is None:
            raise LLMError("llama.cpp isn't built (packaging/build_llama.sh)")
        if hasattr(os, "add_dll_directory"):  # Windows: let hd_llama.dll find llama.dll beside it
            os.add_dll_directory(str(folder))
        lib = ctypes.CDLL(str(folder / LIB_NAME))
        p, i, i32 = ctypes.c_void_p, ctypes.c_int, ctypes.c_int32
        i32s = ctypes.POINTER(ctypes.c_int32)
        for name, args, res in (
            ("hd_init", [ctypes.c_char_p, i], None),
            ("hd_load", [ctypes.c_char_p, i, i, i], p),
            ("hd_free", [p], None),
            ("hd_n_ctx", [p], i),
            ("hd_tokenize", [p, ctypes.c_char_p, i, i32s, i, i], i),
            ("hd_keep", [p, i], None),
            ("hd_decode", [p, i32s, i], i),
            ("hd_next", [p], i32),
            ("hd_is_end", [p, i32], i),
            ("hd_piece", [p, i32, ctypes.c_char_p, i], i),
            ("hd_stop", [p], None),
            ("hd_go", [p], None),
        ):
            fn = getattr(lib, name)
            fn.argtypes, fn.restype = args, res
        lib.hd_init(str(folder).encode(), 0)
        _lib = lib
        return lib


class Stopped(Exception):
    """generate() was stopped before it finished (by stop() or should_stop)."""


class LLM:
    """A loaded chat model. One answer at a time (generate waits for the one before); stop() works from any
    thread, even while a long prompt is still being read."""

    def __init__(self, path: Path, n_ctx: int, threads: int):
        self.lib = _library()
        self._h = self.lib.hd_load(str(path).encode(), n_ctx, threads, threads)
        if not self._h:
            raise LLMError(f"couldn't load the chat model from {path.name} (damaged, or not enough memory)")
        self.n_ctx = self.lib.hd_n_ctx(self._h)
        self._lock = threading.Lock()
        self._kept: list[int] | None = None  # the instructions' tokens, already read

    def tokens(self, text: str) -> list[int]:
        raw = text.encode()
        out = (ctypes.c_int32 * (len(raw) + 8))()  # (never more tokens than bytes)
        n = self.lib.hd_tokenize(self._h, raw, len(raw), out, len(out), 1)
        if n < 0:
            raise LLMError("couldn't read the prompt")
        return list(out[:n])

    def _decode(self, toks: list[int]):
        if not toks:
            return
        arr = (ctypes.c_int32 * len(toks))(*toks)
        r = self.lib.hd_decode(self._h, arr, len(toks))
        if r == 2:
            raise Stopped
        if r != 0:
            raise LLMError(f"llama.cpp couldn't read the prompt (code {r})")

    def _piece(self, tok: int) -> bytes:
        buf = ctypes.create_string_buffer(64)
        n = self.lib.hd_piece(self._h, tok, buf, len(buf))
        if n < 0:
            buf = ctypes.create_string_buffer(-n)
            n = self.lib.hd_piece(self._h, tok, buf, len(buf))
        return buf.raw[:n]

    def generate(
        self, instructions: str, rest: str, max_new: int, should_stop: Callable[[], bool] = lambda: False
    ) -> Iterator[str]:
        """The answer to instructions + rest, a piece of text at a time. Raises Stopped if stopped."""
        with self._lock:
            self.lib.hd_go(self._h)
            first = self.tokens(instructions)
            if first == self._kept:
                self.lib.hd_keep(self._h, len(first))  # reuse them: only the rest is read
            else:
                self.lib.hd_keep(self._h, 0)
                self._kept = None
                self._decode(first)
                self._kept = first
            toks = self.tokens(rest)
            if len(first) + len(toks) + max_new > self.n_ctx:
                raise LLMError(f"the prompt is too long ({len(first) + len(toks)} tokens)")
            self._decode(toks)
            text = codecs.getincrementaldecoder("utf-8")("replace")  # (a character can span two tokens)
            for _ in range(max_new):
                if should_stop():
                    raise Stopped
                tok = self.lib.hd_next(self._h)
                if self.lib.hd_is_end(self._h, tok):
                    break
                if piece := text.decode(self._piece(tok)):
                    yield piece
                self._decode([tok])
            if tail := text.decode(b"", final=True):
                yield tail

    def stop(self):
        """Stop the answer being written (from any thread). The instructions stay read."""
        self.lib.hd_stop(self._h)

    def close(self):
        with self._lock:
            if self._h:
                self.lib.hd_free(self._h)
                self._h = None
