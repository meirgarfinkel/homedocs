# Test fixtures

- `bert-uncased-vocab.txt`: the WordPiece vocabulary of bge-small-en-v1.5 (MIT) and
  ms-marco-MiniLM-L6-v2 (Apache 2.0), which share it (it's bert-base-uncased's, Apache 2.0).
- `tokenizer-cases.json`: token ids Hugging Face's `tokenizers` 0.23 gave for these texts with those
  models' tokenizer.json, which `core/tokenizer.py` must match. Made once with a throwaway script; the
  library isn't a dependency.
