"""Small helpers shared by the whole app."""

import os
import sys


def is_tty() -> bool:
    return sys.stdout is not None and sys.stdout.isatty()


def folder_prefix(folder) -> str:
    """folder with a separator after it: what the paths inside it start with."""
    return str(folder).rstrip("/\\") + os.sep


def inside(path: str, prefixes) -> bool:
    """Is path inside one of the folders these folder_prefix()es are of?"""
    return any(path.startswith(p) for p in prefixes)


def lower_priority():
    """Run the calling thread at low priority, so the computer stays quick while files are read. (On a
    Mac, the whole app's, which is barely noticeable: the window does little work.)"""
    try:
        if os.name == "nt":
            import ctypes

            k32 = ctypes.windll.kernel32
            k32.SetThreadPriority(k32.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
        else:
            os.nice(10)  # (Linux: just this thread)
    except Exception:
        pass
