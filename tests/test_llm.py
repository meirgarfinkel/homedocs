"""Our llama.cpp bindings, against real models: answering, reusing the instructions, stopping mid-prompt.

Skipped unless llama.cpp is built (packaging/build_llama.sh). The first tests use the 1 MB story model in
tests/fixtures; the rest need HOMEDOCS_TEST_GGUF to name a small chat model in Qwen's format (Qwen3-0.6B,
4-bit, is about 400 MB and takes a few seconds here)."""

import os
import threading
import time
from pathlib import Path

import pytest

from homedocs.core import llm

TINY = Path(__file__).parent / "fixtures" / "stories260K.gguf"
MODEL = os.environ.get("HOMEDOCS_TEST_GGUF", "")
pytestmark = pytest.mark.skipif(llm.lib_dir() is None, reason="needs llama.cpp built")
needs_chat_model = pytest.mark.skipif(not Path(MODEL).is_file(), reason="needs HOMEDOCS_TEST_GGUF")

FIRST = (
    "<|im_start|>system\nAnswer in one short sentence, using only the passage. If it doesn't say, reply exactly: "
    '"Not in your files."<|im_end|>\n<|im_start|>user\n'
)
REST = (
    "Passage: Monthly rent is $1,850, due on the first day of each month.\n\nQuestion: How much is the rent?"
    "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
)


def test_the_tiny_model_writes_the_same_story_twice():
    m = llm.LLM(TINY, 256, 2)
    try:
        once = "".join(m.generate("Once upon", " a time", 10))
        assert once.strip()
        assert "".join(m.generate("Once upon", " a time", 10)) == once  # (the first part kept and reused)
        assert "".join(m.generate("Long ago", " a time", 10))  # (a new first part is read afresh)
    finally:
        m.close()


@pytest.fixture(scope="module")
def model():
    m = llm.LLM(Path(MODEL), 2048, 4)
    yield m
    m.close()


@needs_chat_model
def test_it_answers_from_the_passage(model):
    assert "1,850" in "".join(model.generate(FIRST, REST, 40))


@needs_chat_model
def test_reusing_the_instructions_gives_the_same_answer(model):
    once = "".join(model.generate(FIRST, REST, 40))
    assert model._kept is not None
    assert "".join(model.generate(FIRST, REST, 40)) == once  # (greedy: the same input, the same answer)


@needs_chat_model
def test_stop_ends_it_even_while_the_prompt_is_read(model):
    long = REST.replace("Monthly rent", "Monthly rent " + "is high " * 700)
    threading.Timer(0.2, model.stop).start()
    start = time.perf_counter()
    with pytest.raises(llm.Stopped):
        list(model.generate(FIRST, long, 40))
    assert time.perf_counter() - start < 1.5
    assert "1,850" in "".join(model.generate(FIRST, REST, 40))  # (and the next one works)


@needs_chat_model
def test_a_prompt_too_long_for_the_context_is_refused(model):
    with pytest.raises(llm.LLMError):
        list(model.generate(FIRST, "word " * 3000, 40))
