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


def within(path: str, places) -> bool:
    """Is path one of places, or inside one of them (folders)?"""
    return any(path == p or path.startswith(folder_prefix(p)) for p in places)


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


# Where each system keeps its list of trusted certificates (the first that exists is used).
CA_FILES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Pop!_OS, Mint, Arch, Gentoo
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/etc/ssl/cert.pem",  # macOS, Alpine
)
_ssl = None


def ssl_context():
    """For HTTPS: checks servers against this computer's own trusted certificates.

    Python's default doesn't find them in the built app: the Linux one carries AlmaLinux's OpenSSL, which looks
    where only Fedora and RHEL keep them, and the Mac one looks inside python.org's Python. Windows' own store is
    read either way. SSL_CERT_FILE, if set, wins."""
    global _ssl
    if _ssl is None:
        import ssl

        cafile = os.environ.get("SSL_CERT_FILE") or None
        if not cafile and os.name != "nt":
            cafile = next((f for f in CA_FILES if os.path.isfile(f)), None)
        _ssl = ssl.create_default_context(cafile=cafile)
    return _ssl


def open_url(req, timeout: float):
    """urllib.request.urlopen, checked against the computer's trusted certificates (ssl_context)."""
    import urllib.request

    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())
