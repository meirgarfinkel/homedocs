# HomeDocs — requirements

Ask questions about your own documents and get answers quoted from them, with every quote checked against the
file. Fully offline, CPU only, in a desktop app for Linux, Windows and Mac.
Agreed 2026-11-04. **v1** = first release, **v2** = next release, **Later** = not designed now but not ruled out.

## Ground rules (apply to everything)

1. **Read-only.** HomeDocs never creates, changes, moves or deletes anything in the user's folders. Everything
   it writes goes in its own data folder.
2. **Offline.** No network access except downloading the models once (each pinned by sha256) and checking
   GitHub for a new release. No telemetry. Questions, answers and feedback never leave the computer.
3. **Answers only from your files.** The answer is built only from passages found in the user's folders. When
   they don't contain the answer, it says so instead of guessing.
4. **Every claim is cited, every quote is checked.** Each quote is compared with its source text. A quote that
   doesn't match is shown as unverified, never passed off as real.
5. **Runs on 8 GB RAM, CPU only.** No GPU needed. The whole app stays around 2 GB of memory in use, with the
   chat model loaded.
6. **Nothing lost.** Closing mid-index is safe; indexing picks up where it left off. The database survives
   upgrades (backed up first) and is never changed by an older version of the app.
7. **Scale.** Up to a few thousand files (tens of thousands of passages). Search well under 1 s; the answer
   starts appearing within about 1–3 s.
8. **Platforms.** Same as HomePix: one download per platform, nothing else to install. Written in Python with
   a native Qt (PySide6 widgets) interface.
   - Linux: AppImage plus a .deb, x86_64, glibc 2.34 floor (Debian 12+, Ubuntu 22.04+, Fedora, RHEL/Alma 9,
     Arch). Must run on CPUs without AVX2.
   - Windows: per-user installer, no admin. x64.
   - Mac: .app in a .dmg, Apple Silicon, ad hoc signed.

## 1. Folders and files

| Requirement | Priority |
|---|---|
| Add and remove folders to search; their subfolders are included | v1 |
| PDF, Word (.docx), Markdown, plain text | v1 |
| Hidden files and folders, and files over a size limit, are skipped | v1 |
| A folder on a disconnected drive keeps its passages searchable, marked as offline; it isn't treated as deleted | v1 |
| HTML, RTF, OpenDocument (.odt) | Later |
| Scanned PDFs and images (OCR) | Later |

## 2. Indexing

| Requirement | Priority |
|---|---|
| Runs in the background on its own; the window stays usable and search works on what's indexed so far | v1 |
| Text is split into passages of about 300 words (about 400 tokens) with overlap; each keeps its file, pages, headings and position | v1 |
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
| Inline citations ([1], [2]) on every claim, each linked to its passage | v1 |
| Each quote checked against its passage: exact after normalizing spaces and quote marks, then a close fuzzy match. Shown as verified or unverified | v1 |
| "Not in your files" when the passages don't hold the answer | v1 |
| Asking a new question stops the answer in progress | v1 |
| Clicking a citation opens the passage in the Info panel with the quote highlighted, the file name and page, and "Open file" (system app) | v1 |
| Recent questions in the sidebar, reopened without asking the model again | v1 |
| Copy an answer with its sources | v1 |
| Follow-up questions that use the earlier answer (chat) | v2 |
| An optional larger model for machines with 16 GB or more | Later |

## 5. Learning and evals

| Requirement | Priority |
|---|---|
| Thumbs up/down under each answer, and source clicks, are recorded | v1 |
| Feedback becomes per-passage boosts and penalties, applied after reranking with a cap so they can't take over | v1 |
| Every thumbs-up becomes an eval case (question → the passage that answered it) | v1 |
| A hand-made seed eval set over a sample corpus checked into `tests/` | v1 |
| An eval runner reports recall@5 and MRR for vector-only, keyword-only, hybrid, and hybrid + reranker; the numbers go in the README | v1 |
| Forget all feedback (one menu item, with a confirmation) | v1 |
| Fine-tune the reranker on collected feedback, on CPU, kept only if the eval score improves | Later |
| The chat model is never retrained | — |

## 6. The app

| Requirement | Priority |
|---|---|
| First run: pick a folder, download the models with a progress bar, then indexing starts | v1 |
| A failed or interrupted download resumes; a file that fails its sha256 check is deleted and fetched again | v1 |
| Ask page (the main view), Library page (every file, its status, passage count, and failures with the reason) | v1 |
| HomePix's look: same theme tokens, layout (band and well), font, drawn icons; Dark, Light or match the system | v1 |
| Checks for updates quietly after start and from the menu; updates itself per install type | v1 |
| One copy at a time | v1 |
| `--self-test` checks the bundle: llama.cpp loads a tiny model and generates, both ONNX models load, the window opens | v1 |

## Speed budget (typical laptop CPU)

Measured on a 2021 laptop (Intel i7-1165G7, 4 cores) where there's a measurement.

| Step | Target | Measured |
|---|---|---|
| Embed the question | < 50 ms | 5–35 ms |
| Keyword + vector search | < 50 ms | a few ms |
| Re-rank the candidates | < 300 ms | ~0.35 s, **over**: 20 candidates, their first 256 tokens, 8-bit model (30 full-length ones at full precision took 2.6 s). The whole search still fits its 1 s |
| Question to passages on screen | < 1 s | ~0.4 s |
| Embedding while indexing (background) | — | 7–11 passages/s on half the cores: ~30 min for 15,000 passages |
| First answer token | < 2 s |
| Generation | ≥ 10 tokens/s |

## Models (downloaded on first run; final pick in milestone 4)

Candidates, to be compared on the eval set before pinning. Each licence must allow use and redistribution.

| Role | Candidates | Size | Runtime |
|---|---|---|---|
| Embeddings | bge-small-en-v1.5 (MIT); all-MiniLM-L6-v2 as the fast fallback | ~30–130 MB | ONNX Runtime |
| Reranker | ms-marco-MiniLM-L-6-v2 cross-encoder (Apache 2.0), 8-bit; L-4 if it's too slow | ~25–90 MB | ONNX Runtime |
| Chat | Qwen2.5-1.5B-Instruct (Apache 2.0), newer Qwen 1–2B and SmolLM 1.7B to compare; Q4_K_M GGUF | ~1 GB | llama.cpp (llama-cpp-python) |

No PyTorch anywhere: ONNX and llama.cpp keep the bundle small and fast on CPU.

**Minimal dependencies.** Five libraries in all (PySide6-Essentials, numpy, onnxruntime, llama-cpp-python,
pypdfium2); everything else uses the standard library. Never a big library for one small feature.

## Not planned

Cloud or API models; GPU support; editing or organizing documents; web or mobile versions; multiple users or
sharing; syncing between computers; searching email or websites; a command-line interface beyond `--self-test`.
