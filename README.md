# HomeDocs

Ask questions about your own documents and get short answers built from them, with every sentence traced back to
the passage that supports it. Fully offline, CPU only, for Linux, Windows and Mac.

**Download** it from the [latest release](https://github.com/meirgarfinkel/homedocs/releases/latest): the
AppImage or .deb for Linux, the installer for Windows, the disk image for a Mac (Apple Silicon). The first time
it runs, it downloads its three models once (about 1.3 GB), then never needs the internet again.

Work in progress. What's planned is in [REQUIREMENTS.md](REQUIREMENTS.md), and how it fits together in
[ARCHITECTURE.md](ARCHITECTURE.md).

## How accurate is it

Measured with the eval in `homedocs/core/evals.py` (see "Evals and tuning" in ARCHITECTURE.md): 135 questions
about 10 multi-page household documents (a lease, insurance policies, a health plan, appliance manuals...) and
5 US government PDFs (IRS publications, Medicare & You), some with no answer in the files. Settings were tuned on
95 of them; these numbers are from the 40 held back, run once at the end. On a 2021 laptop (Intel i7-1165G7),
November 2026, version 0.5.0:

| | 0.4 | 0.5 |
|---|---|---|
| Right answers | 21/40 (52%) | **27/40 (68%)** |
| First word of the answer, typical (slowest) | 10.3 s (16.0 s) | **4.5 s (6.0 s)** |

| Question type | Right |
|---|---|
| A fact stated in one place | 16/20 |
| A fact split across a page break | 3/3 |
| A row of a table | 4/5 |
| Asked in other words than the file uses | 1/4 |
| Needs arithmetic | 1/4 |
| Needs two documents | 0/1 |
| Not in the files ("Not in your files.") | 2/3 |

What it gets wrong:
- **Arithmetic:** it finds the numbers but adds them up wrong ($300 + 12 × $35 came out as $460).
- **Look-alike documents:** asked about the washer's filter, it answered from the dishwasher manual; asked about
  a fridge warranty that isn't in the files, it gave the washer's.
- **Missed facts:** asked whether bed bugs are covered, it said "Not in your files." though the passage it read
  lists them among what isn't covered.
- **Search misses:** 5 of 40 never had the passage they needed in front of the model.

Every sentence of an answer links to the passage behind it, and one with nothing behind it is marked, so check
anything that matters.
