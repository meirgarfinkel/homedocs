#!/usr/bin/env bash
# Builds HomeDocs.deb from the AppImage: the same app, as a package Ubuntu, Debian, Mint and Pop!_OS
# install with a double-click (it opens in their software installer). It goes in /opt/homedocs, with
# HomeDocs in the applications menu.
#   bash packaging/build_deb.sh [HomeDocs-x86_64.AppImage] [HomeDocs.deb]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
APPIMAGE="$(readlink -f "${1:-$ROOT/HomeDocs-x86_64.AppImage}")"
OUT="$(readlink -f "${2:-$ROOT/HomeDocs.deb}")"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/homedocs/__init__.py")"
# what HomeDocs needs from the system (every desktop has these already), as the AppImage's self-test checks
DEPENDS="libgl1, libegl1, libgbm1, libdrm2, libx11-6, libx11-xcb1, libxcb1, libfontconfig1, libfreetype6"
DEPENDS="$DEPENDS, libharfbuzz0b, libasound2t64 | libasound2, ca-certificates"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Unpacking the AppImage..."
cp "$APPIMAGE" "$WORK/app.AppImage"
chmod +x "$WORK/app.AppImage"
(cd "$WORK" && ./app.AppImage --appimage-extract > /dev/null)

PKG="$WORK/pkg"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/applications" \
  "$PKG/usr/share/icons/hicolor/512x512/apps" "$PKG/usr/share/icons/hicolor/scalable/apps"
cp -r "$WORK/squashfs-root/usr/lib/HomeDocs" "$PKG/opt/homedocs"
cat > "$PKG/usr/bin/homedocs" <<'EOF'
#!/bin/sh
exec /opt/homedocs/HomeDocs "$@"
EOF
chmod 755 "$PKG/usr/bin/homedocs"
cp "$ROOT/homedocs-icon.png" "$PKG/usr/share/icons/hicolor/512x512/apps/homedocs.png"
cp "$ROOT/homedocs-icon.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/homedocs.svg"
# the same entry the AppImage writes for itself (homedocs/ui/app.py), pointing at the installed app
cat > "$PKG/usr/share/applications/homedocs.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=HomeDocs
Comment=Ask questions about your documents, offline
Exec=homedocs
Icon=homedocs
Terminal=false
Categories=Office;Utility;
Keywords=documents;search;questions;pdf;
EOF

cat > "$PKG/DEBIAN/control" <<EOF
Package: homedocs
Version: $VERSION
Architecture: amd64
Maintainer: Meir Garfinkel <meirgarfinkel@users.noreply.github.com>
Installed-Size: $(du -sk "$PKG" | cut -f1)
Depends: $DEPENDS
Section: utils
Priority: optional
Homepage: https://github.com/meirgarfinkel/homedocs
Description: Ask questions about your own documents, offline
 Point it at your folders of PDFs, Word files and notes, then ask questions.
 Answers quote your files, with every quote checked against the source.
 Everything runs on your own computer, on the CPU: nothing is uploaded.
EOF

chmod -R u+rwX,go+rX,go-w "$PKG"  # 755 for folders and programs, 644 for the rest
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$OUT" > /dev/null
echo "Done: $OUT ($(du -h "$OUT" | cut -f1))"
