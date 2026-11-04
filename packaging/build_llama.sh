#!/usr/bin/env bash
# Builds llama.cpp (the pinned release below) and native/hd_llama.c into build/llama/ (or the folder given):
# the libraries core/llm.py loads to write answers, which the app builds bundle. Linux and Mac; Windows has
# build_llama.bat. A second run only rebuilds what changed.
#
#   bash packaging/build_llama.sh [OUT_DIR]
#
# Needs git, a C/C++ compiler and CMake (fetched into build/ with pip if it isn't installed).
# The CPU code is built once per CPU feature level and the best one is picked at run time, so one build runs
# on old CPUs (no AVX2) and new ones alike. No GPU code: HomeDocs is CPU only.
set -euo pipefail

LLAMA_TAG=b11803  # 2026-10-21
LLAMA_COMMIT=a37cc46104eb50900d5ab4a9ba02a0abdac039db
CMAKE_VERSION=4.4.4  # only used if CMake isn't installed

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="${HOMEDOCS_BUILD:-$ROOT/build}"  # (the release container builds in its own folder)
OUT="${1:-$BUILD/llama}"
SRC="$ROOT/build/llama-src"  # (the source is shared)
WORK="$BUILD/llama-build"

if [ ! -d "$SRC/.git" ]; then
  git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$LLAMA_TAG" https://github.com/ggml-org/llama.cpp "$SRC"
fi
got="$(git -C "$SRC" rev-parse HEAD)"
if [ "$got" != "$LLAMA_COMMIT" ]; then
  echo "build/llama-src is at $got, not $LLAMA_TAG ($LLAMA_COMMIT). Delete it and run this again." >&2
  exit 1
fi

CMAKE=cmake
if ! command -v cmake >/dev/null; then
  if [ ! -x "$BUILD/cmake-env/bin/cmake" ]; then
    "${PYTHON:-python3}" -m venv "$BUILD/cmake-env"
    "$BUILD/cmake-env/bin/pip" install --quiet "cmake==$CMAKE_VERSION"
  fi
  CMAKE="$BUILD/cmake-env/bin/cmake"
fi

flags=(
  -DLLAMA_DIR="$SRC" -DCMAKE_BUILD_TYPE=Release
  -DGGML_NATIVE=OFF -DGGML_BACKEND_DL=ON -DGGML_OPENMP=OFF
  -DGGML_METAL=OFF -DGGML_BLAS=OFF -DGGML_VULKAN=OFF -DGGML_CUDA=OFF
  -DLLAMA_CURL=OFF -DLLAMA_BUILD_COMMON=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF
  -DLLAMA_BUILD_TOOLS=OFF -DLLAMA_BUILD_SERVER=OFF
)
if [ "$(uname -m)" = x86_64 ]; then
  flags+=(-DGGML_CPU_ALL_VARIANTS=ON)  # one CPU backend per feature level (SSE4.2 ... AVX-512)
else
  flags+=(-DGGML_CPU_ARM_ARCH=armv8.2-a+dotprod)  # Apple Silicon (every M chip has these)
fi

"$CMAKE" -S "$ROOT/native" -B "$WORK" "${flags[@]}" >/dev/null
jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu)"
"$CMAKE" --build "$WORK" --config Release --parallel "$jobs" >/dev/null 2>"$WORK/errors.log" ||
  { cat "$WORK/errors.log" >&2; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT"
# each library once, under the name the others load it by (libllama.so.0, libllama.0.dylib), plus the CPU
# backends, which are loaded by path at run time
for f in "$WORK"/out/* "$WORK"/bin/*; do
  case "$(basename "$f")" in
    libhd_llama.* | lib*.so.[0-9] | lib*.[0-9].dylib | libggml-cpu*.so) cp -L "$f" "$OUT/" ;;
  esac
done
echo "Built llama.cpp $LLAMA_TAG with hd_llama into $OUT:"
ls "$OUT"
