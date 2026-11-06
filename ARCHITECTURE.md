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
          the database, search, answers and citations, evals, models. Plain Python; never imports ui.
  ui/     the desktop app (PySide6/Qt), built on core: the window, its pages and panels.
```

`core` can be used and tested without a window, and most tests do. `ui` only decides what to show and when;
the rules (what a passage is, how results are ranked, whether a sentence counts as supported) all live in `core`.

| core file | What it does |
|---|---|
| `config.py` | tunable constants (passage size, overlap, candidate counts, fuzzy threshold) and `data_dir()` |
| `settings.py` | `settings.json`: folders, theme, chosen models |
| `db.py` | the schema (`TABLES`), versioned upgrades, backups |
| `parsers.py` | one function per file type: returns the text, where each page starts, and the headings |
| `pdf_layout.py` | a PDF page's text in reading order, from where each character sits |
| `passages.py` | splits a file's text into passages (counted in words), each with its page, headings and position |
| `tokenizer.py` | the BERT WordPiece tokenizer both ONNX models use, in plain Python (reads the model's `vocab.txt`) |
| `indexer.py` | finds new, changed, moved and removed files, reads and saves them; runs passes in the background |
| `library.py` | the folders being searched (add, remove), what's left out (leave out, take back in), and the file list the Library page shows |
| `store.py` | saving and removing passages with their keyword index rows; the passage queries; the vector matrix |
| `search.py` | keyword + vector search, fusion, reranking |
| `answer.py` | decides whether to ask the model, builds the prompt, streams the answer, finds each sentence's source |
| `llm.py` | our bindings to llama.cpp (`ctypes`): load a model, read a prompt, stream tokens, stop |
| `models.py` | downloads models (resumable, sha256-checked) and loads them |
| `evals.py` | runs the eval set, scoring each step of the pipeline and the answers |
| `updates.py` | the GitHub release check (from HomePix) |

## Where things are kept

**In the data folder** (`config.data_dir()`: `~/.local/share/homedocs` on Linux, `%LOCALAPPDATA%\homedocs`
on Windows, `~/Library/Application Support/homedocs` on Mac, or `$HOMEDOCS_HOME` if set, which the tests use):

| | |
|---|---|
| `homedocs.db` | the database (SQLite): files, passages, the keyword index, vectors and questions |
| `models/` | the three models, downloaded once and checked by sha256 |
| `settings.json` | the folders to search, what's left out of them, and the user's choices |
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
- `vectors` (milestone 2): each passage's embedding as a float32 blob, which model made it (so changing models
  re-embeds instead of mixing vectors), and a fingerprint of the text the model read, so a copy of a passage
  reuses its vector (0.9.0)
- `questions` (milestone 3): each question asked, when, the answer text, and each sentence's source (passage
  and where in it), so a recent question reopens without asking the model again

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
| numpy | the vector matrix and model inputs (2.4: the last for Python 3.11) |
| onnxruntime | runs the embedder and reranker (its helpers protobuf, flatbuffers and packaging install with it but aren't imported, so they're not in the app) |
| llama.cpp (compiled, not from pip) | runs the chat model. Built from a pinned release together with `native/hd_llama.c`, our small C file that wraps the calls taking llama.cpp's settings structs (whose layout changes between versions) in plain functions, so `llm.py` never has to copy a struct |
| pypdfium2 | reads PDF text and pages (no dependencies of its own) |

Done with the standard library instead: .docx (`zipfile` + `xml.etree`, not python-docx and lxml), the
llama.cpp bindings (`ctypes`, not llama-cpp-python, which brings jinja2 and diskcache and must be compiled
anyway), tokenizing (`tokenizer.py`, not `tokenizers`, which pulls in
huggingface-hub and an HTTP stack), downloads (`urllib`), keyword search (SQLite's FTS5, built into Python's
`sqlite3`).

## The models

| Role | Runtime | Used by |
|---|---|---|
| Embeddings | ONNX Runtime + `tokenizer.py` | `indexer.py` (passages, in batches), `search.py` (the question) |
| Reranker | ONNX Runtime + `tokenizer.py` | `search.py` |
| Chat | llama.cpp through `llm.py`, GGUF 4-bit | `answer.py` |

`models.py` has one entry per model: its files, each with a URL pinned to an exact Hugging Face revision,
sha256, size, and the licence. Both search models use the same vocabulary, so one tokenizer serves both;
`tests/test_tokenizer.py` checks it against ids from Hugging Face's `tokenizers`.

- **Embedder:** snowflake-arctic-embed-xs, Snowflake's own 8-bit version (23 MB). It has 6 layers to
  bge-small's 12, so it embeds twice as fast, and it found as many of the evals' passages: 83/89 and 33/37
  (bge-small 8-bit: 82 and 32; full precision the same). The evals barely tell embedders apart, since keyword
  search and the re-ranker find most passages (with no search by meaning they still find 80 and 30), so
  embedders are also compared by meaning alone (`--set KEYWORD_CANDIDATES=0`): arctic 83 and 34, bge-small
  81 and 34. Questions get the query prefix (the same as bge's); passages are embedded as `file name > headings` then the text, and the [CLS] output is normalized.
- **Re-ranker:** ms-marco-MiniLM-L6-v2, the 8-bit version (23 MB): about 40% faster than full precision, which
  re-ranking needs (see the speed budget in REQUIREMENTS.md). It reads each passage's first
  `RERANK_MAX_TOKENS`.
- Whether full precision and these sizes are right is for the eval set (milestone 5) to say.

The embedder and reranker load at start (once downloaded); the chat model loads on the first question and stays
loaded. They sit behind small interfaces (`embed(texts)`, `score(question, passages)`,
`generate(prompt) -> token stream`) so the tests swap in fakes (`tests/fakes.py`). Indexing and search share one
embedder; ONNX Runtime sessions are safe to use from several threads.

## What happens, step by step

### First run

1. No folders in `settings.json`: the window opens on the Library page, which shows one "Choose a folder"
   button.
2. `ui/models_ui.py` downloads the search models (46 MB, with `urllib`) on their own thread
   (`workers.run_apart`), each file to a `.part` file that resumes if interrupted, then checks its sha256 and
   renames it into place. A bad hash deletes it; no connection shows a note on the Ask page with "Try again".
   Until they're in, search is by keyword. (The `--self-test` never downloads.)
3. Indexing starts, then embedding. The Ask page works on whatever's indexed so far.

### Indexing

`core/indexer.py` runs one pass at a time on a background thread (a lock file stops two copies of the app
indexing together). For each folder in the settings:

0. Files from folders no longer in the settings, or left out, are removed (removing a folder or leaving
   something out starts a pass).
1. **Scan:** walks the folder, skipping hidden files and folders (dot names, and Windows' hidden flag), Word's
   `~$` lock files, links to folders, what's left out (`settings.json` `"left_out"`: subfolders and single
   files), and kinds of file it doesn't read, and compares what it finds with
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

   A PDF page is read in the order it's laid out, not the order the file draws it (`pdf_layout.py`): its
   characters are put back into lines by position, then the page is cut at the gaps down it between two
   columns of text, and at the gaps across it, and what's left is read a line at a time. The drawn order is
   often wrong: tax software prints the blank form and then every value typed into it from the top again, so
   "Wages ... 1a" and its "41,250" ended up in different passages and a question about wages found neither;
   a W-2 draws its labels and its amounts apart. A gap counts as one between columns only when both sides read
   as lines of text, so a form's amounts and a table's numbers stay on the rows of their labels. Text turned
   sideways (a "Downloaded by" stamp) is kept apart, after the page; text off the page is left out, as before.
   Dot leaders (". . . . . 1a") become one "…": each dot counted as a word, so a passage held only a line or two
   of a form and cut the line's amount off. Checked on a sample of 3,200 pages of real PDFs: the same characters
   as the drawn order, about 4 ms a page; the eval scores are unchanged (83/89, 33/37; by meaning 83 and 34).
5. **Split:** `passages.py` cuts the text into passages of up to 60 words (roughly 90 tokens), counted in
   words so changing the model never means splitting again. Short passages won the eval: the re-ranker reads
   all of each one, and the model's reading budget holds several from different places. Sentences are added
   until the next would pass 60 words; the passage then ends at the last paragraph end past 30 words, or, if
   there's none (one long paragraph), where it is, between sentences (a sentence longer than a passage is cut
   between words). A line break counts as a sentence end (a list item, a table row) unless the next line goes
   on in lowercase: PDF text has a line break at the end of every printed line. The last 15 words or so (whole
   sentences) start the next passage too, at every cut, since a paragraph often leans on the one before it. A
   heading starts a new passage once the current one has 20 words, and never ends one. Each passage keeps its pages, the headings it's under
   (like `Coverage > Deductible`) and its start and end position in the file's text.
6. **Save:** one transaction per file replaces its old passages and keyword index rows, then marks it
   `indexed`, so a crash at any point leaves the file either fully old or fully new; on the next start, anything
   still `pending` is simply done again.
7. **Removed:** files that vanished and weren't found moved are removed with their passages.

**Embedding is its own step** (milestone 2): after a pass, every passage without a vector from the current model
gets one, in batches, the text given to the model being `file name > headings` then the passage, so a passage
knows what it's about even when its own words don't say. Changing models only redoes this step. A passage
whose text (name, headings and words) was embedded already reuses that vector: copies of files are common at
home (a backup, a download kept twice), and in one real library 35% of passages were copies. Passages are
fetched 512 at a time: finding them takes ~0.2 s in a big library, as long as embedding 16. With the faster
model, that library embeds at ~184 passages a second (it was ~22).

In the app, `ui/indexing.py` starts a pass on opening, when the app comes back into focus (at most once a
minute), and from the Library page's "Look for changes", and shows progress at the bottom of the sidebar
(`ui/status.py`: a short line, a thin bar and a ✕ to stop; the full wording on hover). Coming back into
focus never interrupts a running pass (`Indexer.start(restart=False)`): another follows it. Adding or removing
a folder does start it over, and nothing done is lost either way: embedding's progress counts every passage in
the library, so it carries on from its percentage. There's no live file watching: it's unreliable on network drives, and costs resources all the time.
The Library page reads `files` for its table (`library.files()`), on a worker thread.

**Folders** are chosen in Settings (`ui/views/settings_dialog.py`): the folders searched, each with its file
count (and "not connected" when offline), Add folder…, and Remove, which asks first ("Stop searching
~/Documents? Its 1,204 files leave the search; nothing on disk changes."). Under them, what's left out, with
"Leave out a subfolder…" (a folder picker that starts in the chosen folder and only accepts one inside a
searched folder) and "Take back in". Leaving out a folder that holds things already left out replaces them.
On first run the Library page still shows its one "Choose a folder" button.

**The sidebar's folders** (`ui/views/sidebar.py`, as in HomePix): under Library, the folders searched as a tree
(`library.folder_counts()` and `folder_tree()`, on a worker; folders without files are left out), each with
its file count and, if it holds folders, an arrow at its right. A big library has thousands of folders
(8,740 in one test), so a folder's rows are made only when it's opened, and the tree is rebuilt only when the
counts change. Choosing one shows the Library page for just that folder.

**The Library page** is for what's in those folders, file by file: the filters (All, Indexed, Waiting, Failed,
Left out), the name filter, the table, and "Look for changes". Right-click a file: Open, Show in folder, Read
again, Leave out this file, Leave out its folder. A left-out file or folder shows under "Left out", with Take
back in.

### Asking: search

`core/search.py` (`Searcher.search`), on a worker thread (`workers.Latest`, so only the newest question counts):

1. **Keyword:** FTS5 `MATCH` on any of the question's words but the common ones ("what", "is", "the"...: see
   `store.COMMON`), each quoted so punctuation can't break the query; the best `KEYWORD_CANDIDATES` by BM25.
2. **Meaning:** the question's vector against the matrix (`store.VectorIndex`, reloaded after indexing changes
   the vectors); the best `VECTOR_CANDIDATES`.
3. **Merge:** reciprocal rank fusion (`1 / (RRF_K + place)`, summed across both lists).
4. **Re-rank:** the re-ranker scores the best `RERANK_CANDIDATES`, reading each with its file name and headings
   (as it was embedded: "This is your total income ... 71,762" says nothing of a tax return, its file name
   does). Its order is then blended with the merged order (reciprocal rank fusion again, the merged order
   weighted `RERANK_BLEND`), so a passage both agree on wins.
5. Identical passages (the same file in two places) are shown once; the best `SHOW_RESULTS` are kept, with where
   the question's words are in each (FTS5's `highlight()`, so "renewing" marks "renew").

Without the models it's keyword search alone. Search by meaning always returns its nearest passages, even for
a question nothing answers; telling "not in your files" is the answer step's job (milestone 3), from the
re-ranker's scores.

The Ask page (`ui/views/ask.py`) shows each passage's file, page or headings, and a snippet from the sentence
with the first highlighted word. Clicking one shows it whole in the Info panel (`ui/views/passage_panel.py`),
with "Open file" and "Show in folder".

### Asking: the answer

`core/answer.py`, after the search above:

1. **Ask the model at all?** If nothing was found, or the best passage's re-ranker score is below
   `NOT_FOUND_SCORE` (−7), the answer is "Not in your files." at once. Above that, scores of answerable and
   unanswerable questions overlap, so the model decides (see REQUIREMENTS.md, how the chat model was chosen).
2. **Prompt:** the best passages that fit in `ANSWER_CHARS` (1,800 characters, about 550 tokens: what the
   model reads within the 5 s to the first word), in rank order: one that doesn't fit is passed over, and a
   shorter one after it may still fit. They're numbered, each with its file name and page or headings. The instructions: answer in one to three short sentences, only from the passages, and if they
   don't hold the answer reply exactly "Not in your files." The model isn't asked to cite: small models do it
   badly. The prompt is written out in Qwen's chat format, with thinking turned off.
3. **Stream:** `llm.py` reads the prompt and yields tokens one at a time. The instructions are the same for
   every question, so they're read once when the model loads and kept; each question only reads its passages.
   `ui/workers.py` has a streaming job for this: it sends each batch of tokens to the UI thread through the
   same signal relay and checks a cancel flag between tokens (and between batches while reading the prompt),
   so a new question stops the old answer at once.
   **Arithmetic** is the app's, not the model's: small models find the right numbers but add them up wrong
   ("$300 + ($35 × 12) = $460"). Each sum the answer writes out (`... = result`) is worked out by
   `answer.fix_sums` and its result, and any later mention of the wrong one, replaced in the model's style
   ("$", commas, cents). It runs on the text so far as it streams in, so the wrong figure never shows. A
   minus needs spaces ("2022-2025" isn't a sum), and only numbers, + − × ÷ and brackets are worked out.
4. **Find sources:** once finished, the answer is split into sentences (a line wrapped by hand isn't a sentence
   end). For each, every sentence of the passages read is scored two ways: the share of its words (stemmed,
   common words and the question's own words left out, so what it adds, like "45 minutes", is what's checked)
   found there, and how close they are in meaning (the embedder, already loaded). A sentence with nothing to
   check ("Yes.") is left alone. The best match above `SUPPORT_WORDS` or
   `SUPPORT_MEANING` becomes that sentence's link `[n]`, with where it is in the passage for highlighting; no
   match marks the sentence "not found in your files". Known gap: a real fact attached to the wrong thing can
   pass (the car's $500 deductible given for the house matched the car policy's sentence); the evals track it.
5. **Save:** the question, answer and each sentence's source go into `questions`.

### Sources and the Info panel

Clicking `[n]` or a passage shows it in the Info panel (the right dock, from HomePix), with the supporting
sentence highlighted, the file name and page, and "Open file" (`QDesktopServices.openUrl`). The panel has no
button in the top bar: it opens on a click and closes with its ✕ or Ctrl+I.

### Evals and tuning

`core/evals.py` measures how accurate HomeDocs is and where it loses questions. It's a development tool: the
eval set and its documents are never in the app.

**The documents** (`tests/evals/`):
- Public-domain US government publications (IRS guides, Medicare's handbook, a federal employee health-plan
  brochure and the like): long, many pages, tables. Too big for git, so `sources.json` pins each one by URL
  and sha256; the eval downloads them once into `build/evals/` and checks them, as the app does its models.
  Prior-year URLs are used where a publisher replaces its files each year.
- Household documents written for it (a lease, car and home insurance, a health plan, appliance manuals, an
  employee handbook, a tax return and W-2 drawn the way tax software prints them: the blank form, then the
  values), made into multi-page PDFs with tables and fine print by a script from text kept in git (as
  `tests/samples.py` makes the test PDFs), so look-alike facts across files are there on purpose.

**The questions** (`tests/evals/questions.jsonl`, about 100): each has a type (one fact, table, two passages,
two documents, across a page break, reworded, calculation, not in the files, general: asked the way people
ask, "what did I make last year?"), the passages it needs (file and a short quote each, or a list of quotes
when the same fact is in more than one place), the facts a right answer states (patterns), and whether it's for
tuning or held back (`"held_back": true`, about 30%). Each run first checks that every quote is in its file, so
a mistake in the questions isn't blamed on HomeDocs.

**A run** indexes the documents with the real indexer into a data folder under `build/evals/`, kept for the
next run while the documents, the splitting settings and the splitting code are unchanged; then for each
question:

1. **Split:** is each needed quote whole inside one passage?
2. **Search:** is every needed passage among search's `RERANK_CANDIDATES`?
3. **Pick:** after re-ranking, is every needed passage among those the model reads (`answer.pick`)?
4. **Answer:** the chat model answers; it's right if it states every fact (or says "Not in your files" when
   it should).

A failed question is blamed on the first step that lost it. The report gives, per step and per question type,
how many were lost, plus "Not in your files" accuracy, the share of sentences with a source, sentences sourced
from the wrong file, and the median and slowest time to the first word. `--search` stops after step 3 (seconds,
no chat model), to try search settings quickly; `--held-back` runs the held-back questions instead of the
tuning ones; `--only TYPE` runs one type; `--set NAME=VALUE` changes a setting of `config.py` for the run (in
every module that imported it). Each run's answers are saved to `build/evals/results-*.json`.

**Trust signals:** each answer also keeps what a rating of how far to trust it could go by, all known once
it's written (`evals.SIGNALS`: the re-ranker's scores, how well each sentence matches its source, numbers that
aren't in the passages), and the report shows how well each tells right answers from wrong ones. Measured on
the tuning set (0.11, 78 right and 14 wrong answers), none does better than a coin toss (0.47-0.53); on the
held-back set (29 right, 11 wrong) the best two reach 0.66-0.67. Wrong answers come from real passages, with
real numbers from the wrong row or file, and too few are wrong to trust the held-back figures, so nothing
is shown in the app yet.

**Tuning** changes one setting at a time (`core/config.py` holds them all: passage size and overlap,
candidates, how keyword and meaning results are combined, passages the model reads and their length, the "Not in
your files" cutoff, the source check's strictness) and keeps it only if the tuning score improves and the first
word still comes within 5 s. The step losing the most questions goes first. New settings that a step needs
(keeping tables whole, not splitting at page breaks, giving the model the passage beside a match) are added
the same way: measured, kept only if they help. Each kept change's before and after go in the PR.

The held-back questions are run once tuning is done; those numbers go in the README with the date and computer.
Ordinary CI runs the eval with the fake models on a few questions, to check the plumbing.

## Threads

The UI thread never waits on a model or the disk. `ui/workers.py` (from HomePix) runs work on a 2-thread pool
and hands results back through a signal relay; `run_apart` gives downloads and indexing their own thread;
`Latest` is for searches; the streaming job (new) is for answers. Indexing and answering can run at the same
time; SQLite's WAL mode lets search read while indexing writes.

Nor does it do long work of its own while indexing runs. The Library table sorts its rows itself, in one Python
sort (Qt's sorting asks Python for each row's value again and again: 5-10 s for 10,000 files, every 5 s while
indexing, which froze the window), and it only reloads while the page is showing; a hidden one catches up when
it's opened. Measured with 10,000 files: a reload takes 0.15 s on the UI thread, and the indexer's own threads
(reading, embedding) hold it up for at most ~15 ms at a time.

## Memory

Roughly: Python and Qt ~250 MB, chat model ~1.1 GB with its context, embedder and reranker ~200 MB, vectors
and the database cache ~100 MB. Around 2 GB with everything loaded. The context window is kept small
(`N_CTX` in `config.py`, 3,072 tokens) because the passages read and an answer fit in it. Measured with Qwen3-1.7B: 2.4 GB
for a test script holding the chat model and Python (no window).

## The tests

`tests/` runs with `pytest` (see CONTRIBUTING.md). `tests/conftest.py` gives every test its own data folder,
so nothing touches real files, plus the shared helpers: `hd` (core functions with fake models), `docs` (a folder
of generated sample files), `app` and `open_window` for the window, and `wait_until`.

The fakes, like HomePix's `FakeClip`, keep tests fast and offline; CI never downloads a model:
- `FakeEmbedder`: a hashing embedder (words hashed into a fixed-size vector), so similar text gets similar vectors
- `FakeReranker`: scores by word overlap
- `FakeLLM`: streams an answer made of passage 1's first sentence, so its source is found

`test_self_test.py` runs the app's own `--self-test`. `test_migrations.py` upgrades old schemas. `test_sources.py`
covers finding sources (same words, reworded, made up, from the wrong passage). `test_llm.py` runs the
bindings against a tiny model when llama.cpp is built, and is skipped otherwise.

## Packaging (what differs from HomePix)

- **llama.cpp** was the main risk. It's built from a pinned release by `packaging/build_llama.sh` (and a `.bat`
  for Windows) together with `native/hd_llama.c`: inside the AlmaLinux 9 container for Linux (so it passes the
  glibc 2.34 check), with runtime CPU feature detection (`GGML_NATIVE=OFF`, `GGML_CPU_ALL_VARIANTS`, backends
  loaded dynamically) so the same bundle runs on CPUs with and without AVX2. Windows builds it with clang in
  Visual Studio's environment (as llama.cpp's own Windows release does: its CPU code is faster than MSVC's),
  and the Mac for Apple Silicon (ARMv8.2 with dot product, which every M chip has). Each app build runs it
  first; `homedocs.spec` puts the libraries in the bundle's `llama` folder, where `llm.py` finds them (or in
  `build/llama` when running from source).
- **`--self-test`** loads llama.cpp (and fails if none of its CPU backends load) and writes a few words with
  `tests/fixtures/stories260K.gguf`, a 1 MB story model (MIT) that the builds and the distro smoke tests put in
  the data folder as `models/selftest.gguf`. It loads both ONNX models and embeds and reranks one sentence when
  they're in the data folder too, then opens the window and grabs it. The real models are never bundled.
- **Removed from HomePix:** the file-change journal (HomeDocs never changes files), ExifTool, OpenCV, HEIC
  support and photo MimeTypes.

## Where to look

| To change... | Look in |
|---|---|
| passage size, overlap, candidate counts, how many passages the model reads, when a sentence counts as supported | `core/config.py` |
| which file types are read, or how | `core/parsers.py` |
| how files are found, skipped or split | `core/indexer.py` |
| what's stored | `core/db.py` (`TABLES`, then an upgrade step) |
| how results are ranked | `core/search.py` |
| the prompt, or how sources are found | `core/answer.py` |
| the llama.cpp calls | `core/llm.py`, `native/hd_llama.c` |
| which models are used, their URLs and hashes | `core/models.py` |
| the eval set, its documents, or what it measures | `tests/evals/`, `core/evals.py` |
| colours, font, icons (the Paper look: REQUIREMENTS) | `ui/theme.py` |
| the top bar and its menu | `ui/main_window.py`, `ui/menu.py` |
| a page or panel of the window | `ui/views/` |
| the folders searched and what's left out | `core/library.py`, `ui/views/settings_dialog.py` |
| the status line at the bottom of the sidebar | `ui/status.py` |
| streaming and cancelling background work | `ui/workers.py` |
