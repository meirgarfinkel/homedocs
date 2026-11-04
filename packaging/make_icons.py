"""Makes the app icon files (homedocs-icon.png, .ico and .icns) from the logo drawn in code
(theme.brand_mark), so they always match it. Run after changing the logo:
    QT_QPA_PLATFORM=offscreen .venv/bin/python packaging/make_icons.py
The .ico and .icns hold PNG images, written with the standard library (no Pillow needed)."""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from homedocs.ui import theme  # noqa: E402


def png(px: int) -> bytes:
    img = theme.brand_mark(px, 1.0).toImage()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def ico(sizes) -> bytes:
    images = [png(s) for s in sizes]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for s, data in zip(sizes, images, strict=True):
        side = 0 if s >= 256 else s  # 0 means 256
        out += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(images)


def icns() -> bytes:
    # the PNG kinds macOS reads: 16, 32, 128, 256, 512 and 1024 px (the last two also the @2x ones)
    kinds = [(b"icp4", 16), (b"icp5", 32), (b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024)]
    body = b"".join(k + struct.pack(">I", 8 + len(d)) + d for k, d in ((k, png(s)) for k, s in kinds))
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


if __name__ == "__main__":
    app = QApplication([])
    (ROOT / "homedocs-icon.png").write_bytes(png(512))
    (ROOT / "homedocs-icon.ico").write_bytes(ico([16, 24, 32, 48, 64, 128, 256]))
    (ROOT / "homedocs-icon.icns").write_bytes(icns())
    print("made homedocs-icon.png, .ico and .icns")
