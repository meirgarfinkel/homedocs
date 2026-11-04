# Test fixtures

- `bert-uncased-vocab.txt`: the WordPiece vocabulary of bge-small-en-v1.5 (MIT) and
  ms-marco-MiniLM-L6-v2 (Apache 2.0), which share it (it's bert-base-uncased's, Apache 2.0).
- `tokenizer-cases.json`: token ids Hugging Face's `tokenizers` 0.23 gave for these texts with those
  models' tokenizer.json, which `core/tokenizer.py` must match. Made once with a throwaway script; the
  library isn't a dependency.
- `stories260K.gguf`: a 260-thousand-parameter story-writing model (Andrej Karpathy's tinyllamas, MIT), as
  converted for llama.cpp's own tests ([ggml-org/test-model-stories260K](https://huggingface.co/ggml-org/test-model-stories260K),
  revision 479896ec, sha256 270cba1b...). 1 MB: enough for the self-test and `test_llm.py` to check llama.cpp
  loads and writes, without downloading anything.
