"""Getting the search models ready: downloaded the first time (about 155 MB, with progress), then loaded,
then handed to the indexer (to embed passages) and the searcher. Until then, search is by keyword."""

import time

from PySide6.QtCore import QObject, Signal

from ..core import models
from . import workers


class ModelLoader(QObject):
    """progress(done, total): bytes downloaded; ready(): loaded and handed over; failed(message): the
    download or loading failed (start() tries again)."""

    progress = Signal(object, object)
    ready = Signal()
    failed = Signal(str)
    downloads = True  # False: only load models already there (the self-test downloads nothing)

    def __init__(self, indexer, searcher, parent=None):
        super().__init__(parent)
        self.indexer, self.searcher = indexer, searcher
        self.busy = False
        self.loaded = False
        self._stop = False
        self._shown = 0.0

    def start(self):
        if self.busy or self.loaded:
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

    def _report(self, done, total):
        now = time.monotonic()
        if done == total or now - self._shown > 0.2:  # (a few times a second is plenty)
            self._shown = now
            self.progress.emit(done, total)

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
