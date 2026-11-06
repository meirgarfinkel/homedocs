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

| | 0.10 | 0.11 | 0.11.1 | 0.12 | 0.13 |
|---|---|---|---|---|---|
| Right answers | 25/45 (56%) | 30/45 (67%) | 31/45 (69%) | 31/45 (69%) | **34/45 (76%)** |
| Wrong answers given as answers | | 12 | 11 | 8 | **10** |
| First word of the answer, typical (slowest) | 4.9 s (6.9 s) | 5.1 s (7.2 s) | 5.3 s (6.6 s) | 4.9 s (5.5 s) | **4.9 s (6.7 s)** |

0.12 answers with Qwen3.5-2B instead of Qwen3-1.7B. In 0.13, when the files don't answer the question, the
answer says what they do say instead of only "Not in your files.", and an answer the model doubts at the end
is kept: 3 more right, 2 more wrong (both had said "Not in your files." before).

| Question type (0.13) | Right |
|---|---|
| A fact stated in one place | 18/20 |
| Asked the way people talk ("How much of my income was taxed?") | 4/5 |
| A fact split across a page break | 2/3 |
| A row of a table | 4/5 |
| Asked in other words than the file uses | 3/4 |
| Needs arithmetic | 1/4 |
| Needs two documents | 0/1 |
| Not in the files ("Not in your files.") | 2/3 |

What it gets wrong:
- **Arithmetic:** the app does the sums the model writes out, but the model can leave numbers out or not
  write the sum: asked what the furnace's tune-ups and repairs cost in all, it named three of the five bills
  and gave a total that matched none of them.
- **Misreading:** asked what a dog costs in the first year, it read the pet fee and pet rent as starting after
  the first year.
- **Look-alike facts:** asked about a fridge warranty that isn't in the files, it gave another appliance's.
- **Search misses:** 7 of 45 never had the passage they needed in front of the model, among them "What did I
  make last year?" When the model then answers from what it was given, the answer is wrong (6 of the 7).

Every sentence of an answer links to the passage behind it, and one with nothing behind it is marked, so check
anything that matters.
