#!/usr/bin/env bash
# Builds HomeDocs.dmg: the disk image Mac users download, holding HomeDocs.app to drag to Applications.
# Run on a Mac, from anywhere:   bash packaging/build_mac.sh
# Needs Python 3.10-3.12 (python.org, or Homebrew's python@3.12). The app is made for the kind of Mac
# it's built on (Apple Silicon on an M1 or newer). It's signed only "ad hoc" (no Apple Developer
# account), so macOS asks people to allow it once: see the README.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
BUILD="${HOMEDOCS_BUILD:-$ROOT/build}"
ENV="$BUILD/env"
PYTHON="${PYTHON:-python3}"

if [ "$(uname)" != "Darwin" ]; then
  echo "This builds the Mac app, so it runs on a Mac."
  exit 1
fi
mkdir -p "$BUILD"
cd "$BUILD"

echo "1/5  Setting up a clean Python environment for building..."
STAMP="$(shasum -a 256 "$ROOT/requirements.txt" | cut -c1-16)"
if [ "$(cat "$ENV/.requirements" 2>/dev/null)" != "$STAMP" ]; then
  rm -rf "$ENV"
fi
if [ ! -x "$ENV/bin/python" ]; then
  "$PYTHON" -m venv "$ENV"
fi
"$ENV/bin/python" -m pip install --quiet --upgrade pip
"$ENV/bin/python" -m pip install --quiet -r "$ROOT/requirements.txt" pyinstaller
echo "$STAMP" > "$ENV/.requirements"

echo "2/5  Building llama.cpp (a few minutes the first time)..."
bash "$HERE/build_llama.sh" "$BUILD/llama" | tail -n +2 | tr '\n' ' '
echo

echo "3/5  Bundling HomeDocs with PyInstaller..."
rm -rf "$BUILD/dist" "$BUILD/work"
"$ENV/bin/pyinstaller" --noconfirm --clean --log-level ERROR \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" "$HERE/homedocs.spec"
APP="$BUILD/dist/HomeDocs.app"
# one consistent ad hoc signature over the whole app (Apple Silicon runs nothing unsigned)
codesign --force --deep --sign - "$APP"

echo "4/5  Checking that the bundled app starts..."
# (with a data folder of its own, so it never touches yours; models put in build/selftest/models
# are checked too. The tiny test model checks llama.cpp writes.)
mkdir -p "$BUILD/selftest/models"
cp "$ROOT/tests/fixtures/stories260K.gguf" "$BUILD/selftest/models/selftest.gguf"
rm -f "$BUILD/selftest/app.log"
if ! HOMEDOCS_HOME="$BUILD/selftest" "$APP/Contents/MacOS/HomeDocs" --self-test; then
  cat "$BUILD/selftest/app.log" 2>/dev/null || true
  echo "The self-test failed: see above."
  exit 1
fi
cat "$BUILD/selftest/app.log" 2>/dev/null || true

echo "5/5  Making the disk image..."
STAGE="$BUILD/dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/HomeDocs.app"
ln -s /Applications "$STAGE/Applications"  # so people can drag the app onto it
OUT="${HOMEDOCS_OUT:-$ROOT}/HomeDocs.dmg"
rm -f "$OUT"
hdiutil create -quiet -volname HomeDocs -srcfolder "$STAGE" -ov -format UDZO "$OUT"
rm -rf "$STAGE"

echo
echo "Done: $OUT ($(du -h "$OUT" | cut -f1))"
