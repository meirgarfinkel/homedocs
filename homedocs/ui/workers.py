"""Running slow work (disk, models, search) off the UI thread, with results delivered back to it."""

import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal


class _Relay(QObject):
    done = Signal(object, object, object)  # callback, result, error
    part = Signal(object, object)  # callback, a piece of a streamed result


_relay = None
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="homedocs-job")
_pending = 0  # jobs submitted to the pool and not finished yet
_vital = 0  # of those, the ones quitting must wait for
_idle = threading.Condition()


def _get_relay() -> _Relay:
    """Made on the UI thread (the first job is always started there): it delivers on its own thread."""
    global _relay
    if _relay is None:
        _relay = _Relay()
        _relay.done.connect(lambda cb, res, err: cb(res, err))
        _relay.part.connect(lambda cb, value: cb(value))
    return _relay


def _job(fn, callback, args):
    """The work, then its result to the UI thread."""
    relay = _get_relay()

    def job():
        try:
            res, err = fn(*args), None
        except Exception as e:  # handed to the callback, which decides what to show
            res, err = None, e
        relay.done.emit(callback, res, err)

    return job


def _counted(job, must_finish):
    def run_it():
        global _pending, _vital
        try:
            job()
        finally:
            with _idle:
                _pending -= 1
                _vital -= must_finish
                _idle.notify_all()

    return run_it


def run(fn, callback, *args, must_finish=False):
    """Run fn(*args) on a worker thread, then callback(result, error) on the UI thread.
    error is None on success, else the exception (and result None). must_finish: quitting waits for
    it however long it takes (changes to the user's files)."""
    global _pending, _vital
    with _idle:
        _pending += 1
        _vital += must_finish
    return _pool.submit(_counted(_job(fn, callback, args), must_finish))


def wait_idle(timeout: float) -> bool:
    """On quitting: wait for jobs that must finish, then up to timeout seconds for the rest. True if
    every job finished."""
    with _idle:
        _idle.wait_for(lambda: _vital == 0)
        return _idle.wait_for(lambda: _pending == 0, timeout)


def run_apart(fn, callback, *args):
    """Like run, on a thread of its own that doesn't hold up the shared ones or quitting
    (for long downloads)."""
    threading.Thread(target=_job(fn, callback, args), daemon=True).start()


def run_streaming(fn, on_part, callback, *args):
    """fn(emit, *args) on a thread of its own (like run_apart); each emit(value) reaches on_part(value) on the UI
    thread as it happens, then callback(result, error) when fn returns. (For answers, written a word at a time.)"""
    relay = _get_relay()

    def emit(value):
        relay.part.emit(on_part, value)

    threading.Thread(target=_job(lambda *a: fn(emit, *a), callback, args), daemon=True).start()


class Latest:
    """For requests where only the newest answer matters (e.g. the search just typed):
    answers to older requests are dropped."""

    def __init__(self):
        self._n = 0
        self._lock = threading.Lock()

    def run(self, fn, callback, *args):
        with self._lock:
            self._n += 1
            n = self._n
        run(fn, lambda res, err: n == self._n and callback(res, err), *args)
