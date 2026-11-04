# PyInstaller recipe for HomeDocs (used by build_linux.sh, build_windows.bat and build_mac.sh).
# Produces a folder dist/HomeDocs/ containing the app and everything it needs, including its own
# copy of Python and Qt (on a Mac, dist/HomeDocs.app too). The AI models are downloaded on first run.
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
WINDOWS = sys.platform == "win32"
LINUX = sys.platform.startswith("linux")
MAC = sys.platform == "darwin"

# Things that get pulled in by accident and only add size.
excludes = [
    "tkinter", "matplotlib", "IPython", "pytest", "scipy", "pandas", "PyQt5", "PyQt6", "PySide2",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtTest", "PySide6.QtSql", "PySide6.QtDBus",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPrintSupport", "PySide6.QtXml",
    "PySide6.QtConcurrent", "PySide6.QtDesigner", "PySide6.QtUiTools", "PySide6.QtHelp",
    "PySide6.QtSvg", "PySide6.QtSvgWidgets",
    "yaml",  # numpy only uses it to print its build settings
]

datas = [(str(ROOT / "homedocs-icon.png"), "."), (str(ROOT / "homedocs/ui/fonts"), "homedocs/ui/fonts")]

# llama.cpp, built by build_llama.sh (.bat) just before: its libraries go in the bundle's llama folder, where
# core/llm.py looks for them
LLAMA = Path(os.environ.get("HOMEDOCS_BUILD", ROOT / "build")) / "llama"
if not any(LLAMA.glob("*hd_llama*")):
    raise SystemExit(f"homedocs.spec: llama.cpp isn't built in {LLAMA} (packaging/build_llama.sh or .bat)")
binaries = [(str(f), "llama") for f in sorted(LLAMA.iterdir())]

a = Analysis(
    [str(ROOT / "homedocs_app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["PySide6.QtNetwork"],
    excludes=excludes,
    noarchive=False,
)

# PyInstaller also copies the llama.cpp libraries hd_llama links against to the top folder. On Linux and
# Windows only the ones in llama/ are used (each loads the others from its own folder), so the copies go. On a
# Mac they stay: PyInstaller rewrites every library there to load what it links against from the top folder.
if not MAC:
    _llama_names = {f.name for f in LLAMA.iterdir()}
    a.binaries = [b for b in a.binaries if "/" in b[0].replace("\\", "/") or b[0] not in _llama_names]

# Qt plugins the app doesn't use (it draws its own widgets).
SKIP_PLUGINS = {"qmltooling", "printsupport", "sqldrivers", "generic", "networkinformation", "tls"}


def _plugin_dir(dest):
    parts = dest.replace("\\", "/").split("/")
    return parts[3] if len(parts) > 4 and parts[:3] == ["PySide6", "Qt", "plugins"] else None


# ...and image formats it never reads through Qt (PDF pulls in all of Qt's PDF engine).
SKIP_FILES = {"libqpdf.so", "qpdf.dll"}
a.binaries = [b for b in a.binaries if _plugin_dir(b[0]) not in SKIP_PLUGINS
              and b[0].replace("\\", "/").rsplit("/", 1)[-1] not in SKIP_FILES]
a.datas = [d for d in a.datas if not d[0].replace("\\", "/").startswith(("PySide6/Qt/translations/",
                                                                          "PySide6/translations/"))]

if LINUX:
    # Keep only the Qt libraries something bundled really links against: worked out from the real
    # dependencies, so it stays right when Qt updates.
    import re
    import subprocess

    import PySide6

    pyside = Path(PySide6.__file__).parent
    qtlib = pyside / "Qt" / "lib"
    seeds = [Path(src) for dest, src, _ in a.binaries
             if dest.replace("\\", "/").startswith("PySide6/Qt/plugins/")
             or (dest.startswith("PySide6/") and dest.count("/") == 1 and ".so" in dest)]
    env = dict(os.environ, LD_LIBRARY_PATH=f"{qtlib}:{os.environ.get('LD_LIBRARY_PATH', '')}")
    needed, todo = set(), [s for s in seeds if s.exists()]
    while todo:
        out = subprocess.run(["ldd", str(todo.pop())], capture_output=True, text=True, env=env).stdout
        for name, path in re.findall(r"(\S+) => (\S+)", out):
            if name not in needed and path.startswith(str(qtlib)):
                needed.add(name)
                todo.append(Path(path))

    def keep(dest):
        dest = dest.replace("\\", "/")
        if dest.startswith("PySide6/Qt/lib/libQt6") and dest.rsplit("/", 1)[-1] not in needed:
            return False
        return not dest.startswith("PySide6/Qt/qml/")

    before = len(a.binaries) + len(a.datas)
    a.binaries = [b for b in a.binaries if keep(b[0])]
    a.datas = [d for d in a.datas if keep(d[0])]
    print(f"homedocs.spec: kept {len(needed)} Qt libraries, dropped {before - len(a.binaries) - len(a.datas)} files")

    # Libraries every Linux desktop has, which must come from that computer rather than the build
    # machine: graphics libraries match its drivers, and a newer system's drivers or fonts need its
    # own newer C++ runtime and font libraries. (A subset of the AppImage project's excludelist.)
    HOST_LIBS = {
        "libgcc_s.so.1", "libstdc++.so.6", "libmvec.so.1", "libz.so.1", "libexpat.so.1",
        "libGL.so.1", "libEGL.so.1", "libGLX.so.0", "libGLdispatch.so.0", "libOpenGL.so.0", "libglapi.so.0",
        "libgbm.so.1", "libdrm.so.2", "libX11.so.6", "libX11-xcb.so.1", "libxcb.so.1",
        "libfontconfig.so.1", "libfreetype.so.6", "libharfbuzz.so.0", "libfribidi.so.0", "libthai.so.0",
        "libasound.so.2",
    }
    a.binaries = [b for b in a.binaries if b[0].replace("\\", "/").rsplit("/", 1)[-1] not in HOST_LIBS]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HomeDocs",
    console=False,  # no terminal window
    icon=str(ROOT / "homedocs-icon.ico") if WINDOWS else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="HomeDocs", upx=False)

if MAC:
    import re

    version = re.search(r'__version__ = "(.+?)"', (ROOT / "homedocs" / "__init__.py").read_text()).group(1)
    app = BUNDLE(
        coll,
        name="HomeDocs.app",
        icon=str(ROOT / "homedocs-icon.icns"),
        bundle_identifier="io.github.meirgarfinkel.homedocs",
        version=version,
        info_plist={
            "CFBundleDisplayName": "HomeDocs",
            "CFBundleShortVersionString": version,
            "LSMinimumSystemVersion": "14.0",
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.productivity",
        },
    )
