"""Settings you might want to tune, and where HomeDocs keeps its data."""

import os
import sys
from pathlib import Path

DB_NAME = "homedocs.db"

# The kinds of file read, by extension
FILE_TYPES = {".pdf": "PDF", ".docx": "Word", ".md": "Markdown", ".markdown": "Markdown", ".txt": "Text"}
MAX_FILE_MB = 100  # bigger files are listed as skipped, not read

# Passages: counted in words, not a model's tokens, so changing models never means splitting again.
# Short ones (60 words, about 90 tokens) won the eval: the re-ranker reads all of each one, and the model's
# reading budget (ANSWER_CHARS) holds five or so from different places instead of one or two long ones.
PASSAGE_WORDS = 60
OVERLAP_WORDS = 15  # the end of each passage starts the next, so a sentence near the cut is in both
MIN_PASSAGE_WORDS = 30  # a full passage ends at the last paragraph's end past this many words, if any
MIN_SECTION_WORDS = 20  # a heading starts a new passage once the current one has at least this many
MAX_PASSAGE_CHARS = 3000  # text without spaces (a long URL, a table dump) is cut at this length

# Search: the best KEYWORD_CANDIDATES by keyword and VECTOR_CANDIDATES by meaning are merged (reciprocal rank
# fusion, RRF_K), the best RERANK_CANDIDATES of those re-ranked, and SHOW_RESULTS shown. Re-ranking reads each
# passage's first RERANK_MAX_TOKENS tokens (all of a passage, now they're short): 20 take about 0.4 s on a 2021
# laptop. (Starting values: the eval set tunes them.)
KEYWORD_CANDIDATES = 50
VECTOR_CANDIDATES = 50
RRF_K = 60
RERANK_CANDIDATES = 20
RERANK_MAX_TOKENS = 256
SHOW_RESULTS = 8
RERANK_BLEND = 1.0  # how much of the search's own order is kept against the re-ranker's (0: none)
EMBED_BATCH = 8  # passages embedded at a time while indexing (stopping waits for one batch at most)
EMBED_FETCH = 512  # passages fetched from the database at a time to embed (finding them takes ~0.3 s in a big
# library: done for every 16 passages, it took as long as embedding them)

# Answers: the chat model reads the best passages that fit in ANSWER_CHARS characters, and writes at most
# ANSWER_TOKENS. ANSWER_CHARS is the speed budget: 1,800 characters is about 550 tokens, which the model reads
# in about 4.5 s on a 2021 laptop (~120 tokens/s once warm), so the first word comes within 5 s. It isn't asked
# at all when the best passage's re-ranker score is below NOT_FOUND_SCORE: answerable questions scored as low as -5.8
# in the test, unanswerable ones -5.3 to -10.6, so only clearly hopeless ones are cut. A sentence of the answer
# counts as backed by a passage sentence holding SUPPORT_WORDS of its words, or this close in meaning
# (SUPPORT_MEANING, the cosine of their vectors). (Measured starting values: see REQUIREMENTS.md.)
ANSWER_CHARS = 1800
ANSWER_TOKENS = 300
NOT_FOUND_SCORE = -7.0
SUPPORT_WORDS = 0.6
SUPPORT_MEANING = 0.80
N_CTX = 3072  # the chat model's context: instructions, the passages, the question and the answer

RESCAN_GAP = 60  # seconds: coming back to the window looks for changed files at most this often


def data_dir() -> Path:
    """Where the database, models and settings live."""
    if os.environ.get("HOMEDOCS_HOME"):
        base = Path(os.environ["HOMEDOCS_HOME"]).expanduser()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "homedocs"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "homedocs"
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        base = Path(xdg) / "homedocs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_file() -> Path:
    return data_dir() / DB_NAME
