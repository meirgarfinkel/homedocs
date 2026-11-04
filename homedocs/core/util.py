"""Small helpers shared by the whole app."""

import os
import sys


def is_tty() -> bool:
    return sys.stdout is not None and sys.stdout.isatty()


def folder_prefix(folder) -> str:
    """folder with a separator after it: what the paths inside it start with."""
    return str(folder).rstrip(os.sep) + os.sep
