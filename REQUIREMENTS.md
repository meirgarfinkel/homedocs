# HomeDocs — requirements

Ask questions about your own documents and get short answers built from them, with every sentence traced back to
the passage that supports it. Fully offline, CPU only, in a desktop app for Linux, Windows and Mac.
Agreed 2026-11-04. **v1** = first release, **v2** = next release, **Later** = not designed now but not ruled out.

## Ground rules (apply to everything)

1. **Read-only.** HomeDocs never creates, changes, moves or deletes anything in the user's folders. Everything
   it writes goes in its own data folder.
2. **Offline.** No network access except downloading the models once (each pinned by sha256) and checking
   GitHub for a new release. No telemetry. Questions, answers and feedback never leave the computer.
3. **Answers only from your files.** The answer is built only from passages found in the user's folders. When
   they don't contain the answer, it says so instead of guessing.
4. **Every sentence is checked against its source.** The app, not the model, finds the passage sentence that
   supports each sentence of the answer and links to it. A sentence nothing supports is marked as not found in
   the files, never passed off as sourced.
5. **Runs on 8 GB RAM, CPU only.** No GPU needed. The whole app stays around 2 GB of memory in use, with the
   chat model loaded.
6. **Nothing lost.** Closing mid-index is safe; indexing picks up where it left off. The database survives
   upgrades (backed up first) and is never changed by an older version of the app.
7. **Scale.** Up to a few thousand files (tens of thousands of passages). Search well under 1 s; the answer
   starts appearing within about 5 s and is finished within about 7 (see the speed budget).
8. **Platforms.** Same as HomePix: one download per platform, nothing else to install. Written in Python with
   a native Qt (PySide6 widgets) interface.
   - Linux: AppImage plus a .deb, x86_64, glibc 2.34 floor (Debian 12+, Ubuntu 22.04+, Fedora, RHEL/Alma 9,
     Arch). Must run on CPUs without AVX2.
   - Windows: per-user installer, no admin. x64.
   - Mac: .app in a .dmg, Apple Silicon, ad hoc signed.

## 1. Folders and files

| Requirement | Priority |
|---|---|
| Add and remove folders to search, in Settings; their subfolders are included | v1 |
| Leave out a subfolder or a single file (in Settings, or from the Library page); it isn't read or searched, and can be taken back in | v1 |
| Removing a folder asks first, saying how many files leave the search (nothing on disk is touched) | v1 |
| PDF, Word (.docx), Markdown, plain text | v1 |
| Hidden files and folders, and files over a size limit, are skipped | v1 |
| A folder on a disconnected drive keeps its passages searchable, marked as offline; it isn't treated as deleted | v1 |
| HTML, RTF, OpenDocument (.odt) | Later |
| Scanned PDFs and images (OCR) | Later |

## 2. Indexing

| Requirement | Priority |
|---|---|
| Runs in the background on its own; the window stays usable and search works on what's indexed so far | v1 |
| Text is split into passages of about 60 words (about 90 tokens) with overlap; each keeps its file, pages, headings and position | v1 |
| The file name and headings are searched along with each passage, so a passage is found by what its document is about | v1 |
| Unchanged files are skipped (size and modified time, then a content hash) | v1 |
| New, changed and removed files are picked up on start and when the window comes back into focus | v1 |
| Progress saved as it goes; closing mid-index and reopening carries on from there | v1 |
| A file that can't be read (broken, password-protected, no text) is listed with the reason, not retried every time | v1 |
| Live watching of folders while the app is open | v2 |

## 3. Search

| Requirement | Priority |
|---|---|
| Hybrid: keyword search (SQLite FTS5) and vector search, merged by reciprocal rank fusion into ~30 candidates | v1 |
| A small cross-encoder reranks the candidates and keeps the best 4–5 | v1 |
| Shown without an answer too: the top passages with file, page and the matching words highlighted | v1 |
| Limit a search to one folder | v1 |
| Under 1 s from pressing Enter to passages on screen (see the speed budget below) | v1 |

## 4. Answers and citations

| Requirement | Priority |
|---|---|
| One question, one answer. A small local model answers from the top passages and streams in word by word | v1 |
| Each sentence of the answer gets a link ([1], [2]) to the passage that supports it. The app finds it (by the words they share and by meaning); the model isn't asked to cite | v1 |
| A sentence no passage supports is marked "not found in your files" | v1 |
| "Not in your files" when the passages don't hold the answer: at once, without the model, when even the best passage scores clearly low; otherwise the model says so | v1 |
| Asking a new question stops the answer in progress | v1 |
| Clicking a link opens the passage in the Info panel with the supporting sentence highlighted, the file name and page, and "Open file" (system app) | v1 |
| Recent questions in the sidebar, reopened without asking the model again | v1 |
| Copy an answer with its sources | v1 |
| Follow-up questions that use the earlier answer (chat) | v2 |
| An optional larger model for machines with 16 GB or more | Later |

## 5. Accuracy and evals

HomeDocs has to be accurate for someone who never gives it feedback. So the chat model stays Qwen3-1.7B, and
every other step is tuned for it, against a test set much harder than the 20 questions it was chosen with.

| Requirement | Priority |
|---|---|
| An eval set of about 100 questions over two kinds of documents: real public-domain US government publications (long, many pages, tables, look-alike numbers) and household documents written for it (a lease, insurance policies, manuals, a health plan) as multi-page PDFs with tables and fine print | v1 |
| Each question tagged with its type: one fact; a fact in a table; an answer spread over two passages of one document; one that needs two documents; one that crosses a page break; reworded with no words in common; a small calculation; not in the files with a look-alike nearby | v1 |
| Each question lists every passage it needs (by file and a short quote, never a passage id, so it holds when passage size changes) and the facts a right answer states | v1 |
| The set is split once: about 70% for tuning and 30% held back. Settings are tuned on the first part only; the held-back part is run at the end, and its numbers are the ones reported | v1 |
| The eval scores each step of the pipeline separately: whether the needed text stayed in one passage (splitting), is among search's candidates, is among the passages the model reads, and whether the answer is right. Each failed question is blamed on the first step that lost it | v1 |
| It also reports: "Not in your files" when it should be and only then, the share of sentences with a source, sentences sourced from the wrong file, and time to the first word | v1 |
| The four steps are tuned one setting at a time, the step losing the most questions first; a change is kept only if the tuning score improves and the first word still comes within 5 s | v1 |
| The held-back numbers, per question type and per step, go in the README, with what it gets wrong | v1 |
| Thumbs up/down under answers, nudging passages for questions close in meaning to the one voted on (capped), and "Forget all feedback" | Later |
| Fine-tune the reranker on collected feedback, on CPU, kept only if the eval score improves | Later |
| The chat model is never retrained | — |

## 6. The app

| Requirement | Priority |
|---|---|
| First run: pick a folder, download the models with a progress bar, then indexing starts | v1 |
| A failed or interrupted download resumes; a file that fails its sha256 check is deleted and fetched again | v1 |
| Ask page (the main view), Library page (every file, its status, passage count, and failures with the reason) | v1 |
| The Library page manages what's in the folders, file by file (open, show in folder, read again, leave out, take back in); which folders are searched is chosen in Settings | v1 |
| Settings: Folders (add, remove, left out) and the look | v1 |
| The Paper look (below): Dark, Light or match the system | v1 |
| Checks for updates quietly after start and from the menu; updates itself per install type | v1 |
| One copy at a time | v1 |
| `--self-test` checks the bundle: llama.cpp loads a tiny model and generates, both ONNX models load, the window opens | v1 |

### The look: Paper (agreed 2026-11-05)

Modelled on Notion: flat, quiet, a tinted top bar and sidebar around a plain page, no lines between them. It
replaces HomePix's sand-on-ink look. The design board: https://claude.ai/artifact/8EJyAvqhzeFWMWixAU6TKc
(the Paper board).

| | Dark (default) | Light |
|---|---|---|
| Page | `#14171C` | `#FFFFFF` |
| Top bar and sidebar (one tint, no line between them or under the bar) | `#1A1E24` | `#F5F7FA` |
| Fields, Copy button, the chosen passage card | `#242930` | `#EEF1F5` |
| Lines (where there are any) | `#2B3139` | `#E3E7ED` |
| Text | `#E4E8EE` | `#1F2733` |
| Muted text | `#98A2AF` | `#5D6979` |
| Sidebar text: plain white or ink, not blue-grey (the chosen page brighter, the "Recent questions" heading dimmer) | white at 80% | ink at 80% |
| Accent (citation numbers, icons) | `#DDB088` apricot | `#966230` |
| Accent fill (buttons, white text on it) | `#966230` | `#966230` |
| "15 files indexed" | `#86D9A6` light green | `#2E8A55` |
| The supporting sentence's highlight (and the question's words, in bold) | white at 10% | ink at 9% (`#F1F1EF` vanished on the tinted Info panel) |

- **Font:** Inter for everything, answers included (bundled, OFL), in place of Plus Jakarta Sans.
- **Sizes:** the question as a 22 px title over the answer; the answer 16 px; the rest 14 px, small text 12–13 px.
- **Shape:** 14 px corners on cards and buttons, 16 px on the ask box.
- **Top bar:** on the left, over the sidebar: the show/hide sidebar button at the far left (it stays put
  whether the sidebar is open or not), and the HomeDocs icon (`homedocs-icon.svg`) with the name, centred over
  the sidebar. The ask box in the middle. On the right, the indexed-files count, then the menu (☰) at the
  far end; the menu opens leftward, inside the window. No Info button.
- **Sidebar:** 180 px. Ask and Library with icons, then "Recent questions".
- **Status:** at the bottom of the sidebar, never over the page: one short line ("Checking for changes",
  "Indexing 312 of 1,204", "Finishing up · 40%", "Copied"), a thin progress bar under it, and a small ✕ to stop
  indexing. The full wording is on hover. With the sidebar closed, the indexed-files count in the top bar
  shows the short line instead.
- **Icons:** thin line icons in the accent colour.
- **Answer:** the question title, "Answer" and Copy, then the answer with superscript source numbers in the
  accent colour. No timing line.
- **Passages:** "Passages · 4 of 8", then rounded cards with a file icon, the file name and place, and two
  lines of the passage; the top card one step lighter.
- **The Info panel** stays (clicking a source number or passage opens it, with the sentence highlighted), but
  it has no button in the top bar; it closes with its own ✕ or Ctrl+I.

## Speed budget (typical laptop CPU)

Measured on a 2021 laptop (Intel i7-1165G7, 4 cores) where there's a measurement.

| Step | Target | Measured |
|---|---|---|
| Embed the question | < 50 ms | 5–35 ms |
| Keyword + vector search | < 50 ms | a few ms |
| Re-rank the candidates | < 300 ms | ~0.2 s: 20 candidates of 60 words, read whole, 8-bit model. The whole search takes 0.25 s typical, 0.6 s at most (95 eval questions) |
| Question to passages on screen | < 1 s | ~0.4 s |
| Embedding while indexing (background) | — | ~65 passages/s on half the cores (measured on a real 620,000-passage library): the 8-bit model, and the passages to embed fetched 512 at a time (fetching them 16 at a time took as long as embedding them). It was ~22/s before. ~4 min for 15,000 passages |
| First word of the answer | ≤ 5 s | 4.9 s typical, 7.1 s at most (95 eval questions asked back to back, so the laptop is hot). The passages read are capped at 1,800 characters (~550 tokens), read at ~120 tokens/s; the instructions are read once and kept. With 300-word passages on real-length documents it was 10.5 s. The earlier target of 2 s isn't reachable with a 1.7B model on this CPU |
| Writing the answer | ≥ 10 tokens/s | ~20–22 tokens/s; a whole answer in 3–7 s |

Not tried yet: sending only the sentences around the matching words instead of whole passages (a shorter prompt
starts sooner, but may leave out what the answer needs; the evals would have to show it doesn't).

## Models (downloaded on first run)

Pinned to exact files (by sha256, in `core/models.py`). Each licence allows use and redistribution. About
1.2 GB in all.

| Role | Model | Size | Runtime |
|---|---|---|---|
| Embeddings | bge-small-en-v1.5 (MIT), 8-bit (Xenova's conversion): the same eval scores as full precision, 1.5x faster | 34 MB | ONNX Runtime |
| Reranker | ms-marco-MiniLM-L-6-v2 cross-encoder (Apache 2.0), 8-bit | 23 MB | ONNX Runtime |
| Chat | Qwen3-1.7B (Apache 2.0), thinking off, Q4_K_M GGUF | 1.1 GB | llama.cpp, through our own small bindings |

No PyTorch anywhere: ONNX and llama.cpp keep the bundle small and fast on CPU.

### How the chat model and its runtime were chosen (2026-11-04)

20 questions over a test folder (16 answerable from a lease, insurance, manuals and the like; 4 not), each
answered from the 3 best passages HomeDocs' own search found, on the i7-1165G7 with 4 threads:

| Model (4-bit) | Right | Sentences the app could source | First word | Writing |
|---|---|---|---|---|
| **Qwen3-1.7B** | **20/20** | 95% | 4.5–4.8 s | 20–22 tokens/s |
| Qwen2.5-1.5B | 17/20 | 100% | 4.4 s | 23 tokens/s |
| Qwen3-0.6B | 17/20 | 89% | 2.1 s | 42 tokens/s |
| Qwen2.5-0.5B | 13/20 | 100% | 2.2 s | 48 tokens/s |

- **The small models** are twice as fast but get things wrong (a non-refundable fee called refundable, the
  car policy's deductible given for the house).
- **Citations:** asking the model to quote its source word for word failed (0–15% of quotes found in the
  passage; Qwen2.5 ignores the instruction), and asking for `[n]` markers pointed at the wrong passage 25–50%
  of the time. Plain answers whose sources the app finds afterwards were right as often and could be sourced
  89–100% of the time, so that's the design.
- **Runtime:** onnxruntime-genai (prebuilt, no compiling) ran the same model 25% slower to the first word
  (5.6 s against 4.5 s) and got 19/20; llama.cpp is worth its packaging work.
- **5 passages instead of 3** took 7.7 s to the first word with no better answers.
- **The re-ranker's score** can rule out a question only when it's very low (below about −7): two answerable
  questions scored −5.8 and −3.9, and the unanswerable ones −5.3 to −10.6.

**Minimal dependencies.** Four Python libraries in all (PySide6-Essentials, numpy, onnxruntime, pypdfium2), plus
llama.cpp itself, compiled from a pinned release and called through `ctypes`; everything else uses the
standard library. Never a big library for one small feature.

## Not planned

Cloud or API models; GPU support; editing or organizing documents; web or mobile versions; multiple users or
sharing; syncing between computers; searching email or websites; a command-line interface beyond `--self-test`.
