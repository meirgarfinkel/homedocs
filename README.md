# HomeDocs

Ask questions about your own documents and get short answers built from them, with every sentence traced back to
the passage that supports it. Fully offline, CPU only, for Linux, Windows and Mac.

**Download** it from the [latest release](https://github.com/meirgarfinkel/homedocs/releases/latest): the
AppImage or .deb for Linux, the installer for Windows, the disk image for a Mac (Apple Silicon). The first time
it runs, it downloads its three models once (about 1.3 GB), then never needs the internet again.

Work in progress. What's planned is in [REQUIREMENTS.md](REQUIREMENTS.md), and how it fits together in
[ARCHITECTURE.md](ARCHITECTURE.md).

## How accurate is it

Measured with the eval in `homedocs/core/evals.py` (see "Evals and tuning" in ARCHITECTURE.md): 149 questions
about 12 multi-page household documents (a lease, insurance policies, a health plan, appliance manuals, a tax
return and W-2...) and 5 US government PDFs (IRS publications, Medicare & You), some with no answer in the files.
Settings were tuned on 104 of them; these numbers are from the 45 held back, run once at the end. On a 2021
laptop (Intel i7-1165G7), November 2026:

| | 0.10 | 0.11 | 0.11.1 | 0.12 |
|---|---|---|---|---|
| Right answers | 25/45 (56%) | 30/45 (67%) | 31/45 (69%) | **31/45 (69%)** |
| Wrong answers (the rest say "Not in your files.") | | | 6 | **3** |
| First word of the answer, typical (slowest) | 4.9 s (6.9 s) | 5.1 s (7.2 s) | 5.3 s (6.6 s) | **4.9 s (5.5 s)** |

0.12 answers with Qwen3.5-2B instead of Qwen3-1.7B: as many right, half as many wrong (it says "Not in your
files." instead), and a little faster.

| Question type (0.12) | Right |
|---|---|
| A fact stated in one place | 18/20 |
| Asked the way people talk ("How much of my income was taxed?") | 2/5 |
| A fact split across a page break | 2/3 |
| A row of a table | 4/5 |
| Asked in other words than the file uses | 1/4 |
| Needs arithmetic | 2/4 |
| Needs two documents | 0/1 |
| Not in the files ("Not in your files.") | 2/3 |

What it gets wrong:
- **Arithmetic:** the app does the sums the model writes out, but the model can leave numbers out: asked what
  the furnace's tune-ups and repairs cost in all, it added three of the five bills.
- **Look-alike facts:** asked about a fridge warranty that isn't in the files, it gave another appliance's.
- **Missed facts:** 4 of 45 got "Not in your files." though the passage it read had the answer, mostly when the
  question is worded unlike the file: "Does my job help pay for new glasses?" against "$150 toward glasses".
- **Search misses:** 7 of 45 never had the passage they needed in front of the model, among them "What did I
  make last year?"

Every sentence of an answer links to the passage behind it, and one with nothing behind it is marked, so check
anything that matters.
