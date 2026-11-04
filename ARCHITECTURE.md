# How HomeDocs fits together

A map of the code for anyone changing it. What the app does is in [README.md](README.md), what's in scope in
[REQUIREMENTS.md](REQUIREMENTS.md), and how to work on it in [CONTRIBUTING.md](CONTRIBUTING.md). Every file
starts with a short description of what it's for; this page is how they connect.

HomeDocs is built on the HomePix base (theme, layout, workers, database upgrades, updates, packaging). What's
the same is described in HomePix's own docs; this page covers what HomeDocs adds and changes.

## Two layers

```
homedocs/
  core/   everything HomeDocs does, with no user interface: reading files, splitting them into passages,
          the database, search, answers and citations, feedback, evals, models. Plain Python; never imports ui.
  ui/     the desktop app (PySide6/Qt), built on core: the window, its pages and panels.
```

`core` can be used and tested without a window, and most tests do. `ui` only decides what to show and when;
the rules (what a passage is, how results are ranked, whether a quote counts as verified) all live in `core`.

| core file | What it does |
|---|---|
| `config.py` | tunable constants (passage size, overlap, candidate counts, boost cap, fuzzy threshold) and `data_dir()` |
| `settings.py` | `settings.json`: folders, theme, chosen models |
| `db.py` | the schema (`TABLES`), versioned upgrades, backups |
| `parsers.py` | one function per file type: returns the text, where each page starts, and the headings |
| `passages.py` | splits a file's text into passages (counted in words), each with its page, headings and position |
| `tokenizer.py` | the BERT WordPiece tokenizer both ONNX models use, in plain Python (reads the model's `vocab.txt`) |
| `indexer.py` | finds new, changed, moved and removed files, reads and saves them; runs passes in the background |
| `library.py` | the folders being searched (add, remove) and the file list the Library page shows |
| `store.py` | saving and removing passages with their keyword index rows; the passage queries; the vector matrix |
| `search.py` | keyword + vector search, fusion, reranking, feedback boosts |
| `answer.py` | builds the prompt, streams the answer, reads its citations and checks each quote |
| `feedback.py` | records thumbs and clicks, works out boosts, turns thumbs-up into eval cases |
| `models.py` | downloads models (resumable, sha256-checked) and loads them |
| `evals.py` | runs the eval set and reports recall@k and MRR |
| `updates.py` | the GitHub release check (from HomePix) |

## Where things are kept

**In the data folder** (`config.data_dir()`: `~/.local/share/homedocs` on Linux, `%LOCALAPPDATA%\homedocs`
on Windows, `~/Library/Application Support/homedocs` on Mac, or `$HOMEDOCS_HOME` if set, which the tests use):

| | |
|---|---|
| `homedocs.db` | the database (SQLite): files, passages, the keyword index, vectors, questions and feedback |
| `models/` | the three models, downloaded once and checked by sha256 |
| `settings.json` | the folders to search and the user's choices |
| `window.ini` | window size and layout |
| `backups/` | a copy of the database before each upgrade |
| `app.log` | the app's output when it's started from the menu (no terminal) |

**The user's folders** are only ever read. HomeDocs keeps nothing inside them.

## The database

`core/db.py` holds the whole schema in one place (`TABLES`), with a comment on every column:

- `files`: one row per file found: its path, name, size, modified time, content hash, status (`pending`,
  `indexed`, `failed`, `skipped`), the error if it failed or was skipped, and whether its folder is offline
  (a flag beside the status, so a file that comes back online is just as it was)
- `chunks`: one row per passage: its file, its place in the file, the pages it starts and ends on (PDFs), the
  headings it's under, its start and end position in the file's text, and the text
- `chunks_fts`: an FTS5 index (Porter stemming, accents ignored) over each passage's text, headings and file
  name, through the `chunk_search` view, so it keeps no copy of the text. `store.py` adds and removes its rows
  in the same transaction as the passages, never with triggers: removing a row needs exactly the text it was
  added with.
- `vectors` (milestone 2): each passage's embedding as a float32 blob, plus which model made it (so changing models
  re-embeds instead of mixing vectors)
- `questions` (milestone 3): each question asked, when, the answer text, and which passages it cited
- `feedback` (milestone 5): question, passage, kind (`up`, `down`, `click`) and when

A schema change bumps `VERSION` and adds an upgrade step to `UPGRADES`. Every upgrade backs the database up
first and runs in one transaction, so a failed one leaves it untouched. A database from a newer version is
refused. Only SQL that SQLite 3.34 understands is used: that's the version in the Linux build.

**Vectors live in numpy at run time, not in a SQLite extension.** `store.py` loads every vector into one
normalized `float32` matrix at start (a few tens of thousands of rows × 384 is tens of MB) and appends to it as
indexing adds passages. Vector search is one matrix product. Extension loading is unreliable in bundled
Python, so there isn't one.

## Dependencies

Kept to the minimum. A library is added only when the standard library can't do the job well, and never a big
library for one small feature: then a focused one is found, or it's written here. Each pin in
`requirements.txt` has a comment saying why.

| Library | Why it's needed |
|---|---|
| PySide6-Essentials | the window |
| numpy | the vector matrix and model inputs |
| onnxruntime | runs the embedder and reranker |
| llama-cpp-python | runs the chat model |
| pypdfium2 | reads PDF text and pages (no dependencies of its own) |

Done with the standard library instead: .docx (`zipfile` + `xml.etree`, not python-docx and lxml), quote
matching (`difflib`, not rapidfuzz), tokenizing (`tokenizer.py`, not `tokenizers`, which pulls in
huggingface-hub and an HTTP stack), downloads (`urllib`), keyword search (SQLite's FTS5, built into Python's
`sqlite3`).

## The models

| Role | Runtime | Used by |
|---|---|---|
| Embeddings | ONNX Runtime + `tokenizer.py` | `indexer.py` (passages, in batches), `search.py` (the question) |
| Reranker | ONNX Runtime + `tokenizer.py` | `search.py` |
| Chat | llama.cpp through llama-cpp-python, GGUF 4-bit | `answer.py` |

`models.py` has one entry per model: URL, sha256, size and licence. The embedder and reranker load at start
(small and quick); the chat model loads on the first question and stays loaded. All three sit behind small
interfaces (`embed(texts)`, `rerank(query, passages)`, `generate(prompt) -> token stream`) so the tests swap in
fakes.

## What happens, step by step

### First run

1. No folders in `settings.json`: the window opens on the Library page, which shows one "Choose a folder"
   button.
2. `models.py` downloads the three models (with `urllib`) on their own thread (`workers.run_apart`), each to a `.part` file
   that resumes if interrupted, then checks its sha256 and renames it into place. A bad hash deletes it and
   tries again.
3. Indexing starts. The Ask page works on whatever's indexed so far.

### Indexing

`core/indexer.py` runs one pass at a time on a background thread (a lock file stops two copies of the app
indexing together). For each folder in the settings:

0. Files from folders no longer in the settings are removed (removing a folder in the Library starts a pass).
1. **Scan:** walks the folder, skipping hidden files and folders (dot names, and Windows' hidden flag), Word's
   `~$` lock files, links to folders, and kinds of file it doesn't read, and compares what it finds with
   `files`. A folder that can't be reached, or that has emptied completely while files were known in it (an
   unplugged drive's mount point looks like that), marks its files offline and keeps them. Files over 100 MB
   are listed as `skipped`.
2. **Skip what hasn't changed:** same size and modified time means skip. If those changed, the file becomes
   `pending`, and when its turn comes the content hash decides: a touched-but-identical file goes back to what
   it was (indexed, or failed) without being read.
3. **Moves:** a pending file whose hash matches a file that vanished in this pass is that file, moved or
   renamed: it takes over its row and passages (and is re-indexed under its new name for keyword search), so a
   reorganized folder isn't read, or embedded, again.
4. **Read:** pending files, newest first. `parsers.py` turns the file into plain text plus where each page starts
   and where each heading is (PDF via pypdfium2, with its bookmarks as headings; Word by reading the paragraphs
   and tables out of the .docx's XML with `zipfile` and `xml.etree`, headings from their styles; Markdown read as
   written, `#` and underlined headings found; text decoded as UTF-8, UTF-16 or Windows-1252). A failure stores
   the reason (a scanned PDF, a password, a damaged file) and the file is marked `failed`, not retried until it
   changes.
5. **Split:** `passages.py` cuts the text into passages of about 300 words (roughly 400 tokens), counted in
   words so changing the model never means splitting again. It breaks between paragraphs, else sentences, else
   words; the last 50 words or so (whole sentences) start the next passage too. A heading starts a new passage
   once the current one has some text, and never ends one. Each passage keeps its pages, the headings it's under
   (like `Coverage > Deductible`) and its start and end position in the file's text.
6. **Save:** one transaction per file replaces its old passages and keyword index rows, then marks it
   `indexed`, so a crash at any point leaves the file either fully old or fully new; on the next start, anything
   still `pending` is simply done again.
7. **Removed:** files that vanished and weren't found moved are removed with their passages.

**Embedding is its own step** (milestone 2): after a pass, every passage without a vector from the current model
gets one, in batches, the text given to the model being `file name > headings` then the passage, so a passage
knows what it's about even when its own words don't say. Changing models only redoes this step.

In the app, `ui/indexing.py` starts a pass on opening, when the app comes back into focus (at most once a
minute), and from the Library page's "Look for changes", and shows progress in the status pill with a Stop
button. There's no live file watching: it's unreliable on network drives, and costs resources all the time.
The Library page reads `files` for its table (`library.files()`), on a worker thread.

### Asking: search

`core/search.py`, on a worker thread (`workers.Latest`, so only the newest question counts):

1. Embed the question.
2. **Keyword:** FTS5 `MATCH` on the question's words (cleaned so punctuation can't break the query), top 50 by
   bm25.
3. **Vector:** the question's vector against the matrix, top 50.
4. **Fuse:** reciprocal rank fusion (`1 / (k + rank)`, summed across both lists) keeps the best ~30.
5. **Rerank:** the cross-encoder scores each (question, passage) pair; the best 4–5 are kept.
6. **Feedback:** each passage's boost from `feedback.py` is added, capped (`BOOST_CAP` in `config.py`), and
   the list re-sorted.

The passages appear on the Ask page right away, before any answer.

### Asking: the answer

`core/answer.py`:

1. **Prompt:** the passages are numbered `[1]`…`[5]` with their file name and page. The instructions say to
   answer only from them, to cite each claim with its number, to quote the exact words that support it, and to
   reply with a fixed "not in your files" line when they don't hold the answer.
2. **Stream:** tokens come from llama.cpp one at a time. `ui/workers.py` has a streaming job for this: it
   sends each batch of tokens to the UI thread through the same signal relay, and it checks a cancel flag
   between tokens, so a new question stops the old answer at once.
3. **Read citations:** once finished, the answer is parsed for `[n]` markers and the quotes next to them.
   Citations to numbers that weren't given are dropped and flagged.
4. **Check quotes:** each quote is compared with its passage: first exact, after normalizing whitespace, curly
   quotes, dashes and case; failing that, the best close match over windows of the passage (`difflib`), above
   `QUOTE_MATCH_MIN`. A match stores where in the passage the quote is (for highlighting); no match marks it
   unverified.
5. **Save:** the question, answer and cited passages go into `questions`.

### Citations and the Info panel

Clicking `[n]` shows the passage in the Info panel (the right dock, from HomePix), with the matched quote
highlighted, the file name and page, and "Open file" (`QDesktopServices.openUrl`). The click is recorded as
feedback.

### Feedback

`core/feedback.py`:

1. Thumbs up or down under an answer records a row per cited passage; a source click records `click`.
2. **Boosts:** a passage's boost comes from its ups, downs and clicks, smoothed so one vote doesn't swing it
   and capped so feedback can nudge the reranker's order but not override it.
3. **Eval cases:** a thumbs-up turns (question, cited passages) into an eval case. "Forget all feedback" clears
   both.

### Evals

`core/evals.py` loads the seed set (`tests/evals/seed.jsonl`, questions over the sample corpus in
`tests/evals/corpus/`) plus any feedback cases, runs each question through search in four modes (vector only,
keyword only, hybrid, hybrid + reranker), and reports recall@5 and MRR per mode. A pytest marker runs it on the
seed set with the real models; ordinary CI runs it with the fakes to check the plumbing. The real numbers are
copied into the README by hand when they change.

## Threads

The UI thread never waits on a model or the disk. `ui/workers.py` (from HomePix) runs work on a 2-thread pool
and hands results back through a signal relay; `run_apart` gives downloads and indexing their own thread;
`Latest` is for searches; the streaming job (new) is for answers. Indexing and answering can run at the same
time; SQLite's WAL mode lets search read while indexing writes.

## Memory

Roughly: Python and Qt ~250 MB, chat model ~1.1 GB with its context, embedder and reranker ~200 MB, vectors
and the database cache ~100 MB. Around 2 GB with everything loaded. The context window is kept small
(`N_CTX` in `config.py`) because five passages and an answer fit in 4k tokens.

## The tests

`tests/` runs with `pytest` (see CONTRIBUTING.md). `tests/conftest.py` gives every test its own data folder,
so nothing touches real files, plus the shared helpers: `hd` (core functions with fake models), `docs` (a folder
of generated sample files), `app` and `open_window` for the window, and `wait_until`.

The fakes, like HomePix's `FakeClip`, keep tests fast and offline; CI never downloads a model:
- `FakeEmbedder`: a hashing embedder (words hashed into a fixed-size vector), so similar text gets similar vectors
- `FakeReranker`: scores by word overlap
- `FakeLLM`: streams a fixed answer citing `[1]` with a quote taken from passage 1

`test_self_test.py` runs the app's own `--self-test`. `test_migrations.py` upgrades old schemas. `test_quotes.py`
covers quote checking (exact, normalized, fuzzy, made up).

## Packaging (what differs from HomePix)

- **llama.cpp is the main risk.** llama-cpp-python is built from source inside the AlmaLinux 9 container (so it
  passes the glibc 2.34 check), with runtime CPU feature detection (`GGML_NATIVE=OFF`, `GGML_CPU_ALL_VARIANTS`,
  backends loaded dynamically) so the same bundle runs on CPUs with and without AVX2. Windows and Mac build it
  the same way in their CI jobs.
- **`--self-test`** loads a tiny GGUF model and generates a few tokens, loads both ONNX models and embeds and
  reranks one sentence, then opens the window and grabs it. The tiny test models are cached in `build.yml`
  (keyed on their sha256) so builds don't download them every time; the real models are never bundled.
- **Removed from HomePix:** the file-change journal (HomeDocs never changes files), ExifTool, OpenCV, HEIC
  support and photo MimeTypes.

## Where to look

| To change... | Look in |
|---|---|
| passage size, overlap, candidate counts, boost cap, quote strictness | `core/config.py` |
| which file types are read, or how | `core/parsers.py` |
| how files are found, skipped or split | `core/indexer.py` |
| what's stored | `core/db.py` (`TABLES`, then an upgrade step) |
| how results are ranked | `core/search.py` |
| the prompt, or how quotes are checked | `core/answer.py` |
| which models are used, their URLs and hashes | `core/models.py` |
| how feedback changes ranking | `core/feedback.py` |
| the eval set or the metrics | `tests/evals/`, `core/evals.py` |
| colours, font, icons | `ui/theme.py` |
| a page or panel of the window | `ui/views/` |
| streaming and cancelling background work | `ui/workers.py` |
