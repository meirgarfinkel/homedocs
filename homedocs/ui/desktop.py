"""Working with the rest of the desktop: opening a file in its usual app, and showing it in the file
manager."""

import os
import shutil
import subprocess
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def _host_env() -> dict:
    """The environment for another app. In the bundled app, its own libraries must not leak into it."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False) or os.environ.get("APPIMAGE"):
        for var in ("LD_LIBRARY_PATH", "QT_PLUGIN_PATH", "PYTHONHOME", "PYTHONPATH"):
            orig = os.environ.get(f"{var}_ORIG")
            if orig:
                env[var] = orig
            else:
                env.pop(var, None)
    return env


def _launch(args) -> bool:
    try:
        subprocess.Popen(
            args, env=_host_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        return True
    except OSError:
        return False


def open_file(path: str) -> bool:
    """Open in the app the desktop uses for that kind of file."""
    if sys.platform.startswith("linux") and shutil.which("xdg-open"):
        return _launch(["xdg-open", path])
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def show_in_folder(path: str) -> bool:
    """Open the file manager at the file's folder, with the file selected where it can do that."""
    if sys.platform.startswith("linux") and shutil.which("dbus-send"):
        url = QUrl.fromLocalFile(path).toString()
        try:
            r = subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--print-reply",
                    "--dest=org.freedesktop.FileManager1",
                    "/org/freedesktop/FileManager1",
                    "org.freedesktop.FileManager1.ShowItems",
                    f"array:string:{url}",
                    "string:",
                ],
                env=_host_env(),
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    if sys.platform == "win32":  # (Explorer reads its command line its own way: the path quoted after the comma)
        return _launch(f'explorer /select,"{os.path.normpath(path)}"')
    if sys.platform == "darwin":
        return _launch(["open", "-R", path])
    return open_file(os.path.dirname(path) if os.path.isfile(path) else path)
