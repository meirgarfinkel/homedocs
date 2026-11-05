"""Getting the models ready. First the search models: downloaded the first time (about 155 MB, with progress),
loaded, and handed to the indexer (to embed passages) and the searcher; until then, search is by keyword. Then
the chat model (1.1 GB), downloaded the same way; it's loaded on the first question. Until it's in, the Ask page
shows passages without an answer. (Without llama.cpp built, there are no answers and nothing to download.)"""

import time

from PySide6.QtCore import QObject, Signal

from ..core import llm, models
from . import workers


class ModelLoader(QObject):
    """progress(done, total): bytes downloaded; ready(): loaded and handed over; failed(message): the
    download or loading failed (start() tries again). The same three for the chat model: answers_progress,
    answers_ready (downloaded: answers can be asked for), answers_failed."""

    progress = Signal(object, object)
    ready = Signal()
    failed = Signal(str)
    answers_progress = Signal(object, object)
    answers_ready = Signal()
    answers_failed = Signal(str)
    downloads = True  # False: only load models already there (the self-test downloads nothing)

    def __init__(self, indexer, searcher, parent=None):
        super().__init__(parent)
        self.indexer, self.searcher = indexer, searcher
        self.busy = False
        self.loaded = False
        self.answers_busy = False
        self.answers = False  # the chat model is downloaded and llama.cpp is there
        self._stop = False
        self._shown = 0.0

    def start(self):
        if self.loaded:
            self.start_answers()
            return
        if self.busy:
            return
        self.busy = True
        if all(m.ready() for m in models.MODELS):
            workers.run(self._load, self._done)
        elif self.downloads:
            workers.run_apart(self._download_and_load, self._done)
        else:
            self.busy = False

    def stop(self):
        self._stop = True

    def _report(self, done, total, signal=None):
        now = time.monotonic()
        if done == total or now - self._shown > 0.2:  # (a few times a second is plenty)
            self._shown = now
            (signal or self.progress).emit(done, total)

    def _download_and_load(self):
        if not models.download(models.MODELS, self._report, lambda: self._stop):
            return None
        return self._load()

    @staticmethod
    def _load():
        return models.Embedder(), models.Reranker()

    def _done(self, loaded, err):
        self.busy = False
        if err is not None:
            print(f"models: {err!r}")
            msg = str(err) if isinstance(err, models.DownloadError) else f"Couldn't load the search models: {err}"
            self.failed.emit(msg)
            return
        if loaded is None:  # stopped (the app is closing)
            return
        embedder, reranker = loaded
        self.indexer.embedder = embedder
        workers.run(lambda: self.searcher.set_models(embedder, reranker), self._handed_over)

    def _handed_over(self, _, err):
        if err is not None:
            self.failed.emit(f"Couldn't load the search models: {err}")
            return
        self.loaded = True
        self.ready.emit()
        self.start_answers()

    # --- the chat model ---------------------------------------------------------------------------------

    def start_answers(self):
        if self.answers or self.answers_busy or not llm.available():
            return
        if models.CHAT.ready():
            self.answers = True
            self.answers_ready.emit()
        elif self.downloads:
            self.answers_busy = True
            workers.run_apart(self._download_chat, self._chat_done)

    def _download_chat(self):
        report = lambda d, t: self._report(d, t, self.answers_progress)  # noqa: E731
        return models.download([models.CHAT], report, lambda: self._stop)

    def _chat_done(self, finished, err):
        self.answers_busy = False
        if err is not None:
            print(f"chat model: {err!r}")
            self.answers_failed.emit(str(err))
        elif finished:
            self.answers = True
            self.answers_ready.emit()
