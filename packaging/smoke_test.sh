#!/usr/bin/env bash
# Runs the AppImage's self-test inside a fresh container of another Linux distribution, set up
# like a normal desktop (graphics, fonts, X libraries) but with nothing else installed.
#   bash packaging/smoke_test.sh debian:12 [HomeDocs-x86_64.AppImage] [models folder]
# Given HomeDocs.deb instead (Debian and Ubuntu only), it installs that the way the software installer
# does (which also checks everything it depends on can be found) and tests the installed app.
# With a models folder (holding the downloaded models), it also checks they run. llama.cpp is always checked,
# with the tiny test model in tests/fixtures.
set -euo pipefail
TINY="$(cd "$(dirname "$0")/.." && pwd)/tests/fixtures/stories260K.gguf"

IMAGE="$1"
APP="$(readlink -f "${2:-HomeDocs-x86_64.AppImage}")"
MODELS="${3:-}"
ENGINE="${CONTAINER_ENGINE:-$(command -v podman || command -v docker || true)}"

# What a desktop install of each family already has, plus a virtual screen (Xvfb).
DEB="xvfb xauth libgl1 libegl1 libgbm1 libdrm2 libx11-6 libx11-xcb1 libxcb1 libfontconfig1 libfreetype6"
DEB="$DEB libharfbuzz0b fonts-dejavu-core ca-certificates"
case "$IMAGE" in
  debian:*|ubuntu:*)  # the sound library was renamed in newer releases
    SETUP="export DEBIAN_FRONTEND=noninteractive; apt-get update -qq >/dev/null
      && { apt-get install -y -qq --no-install-recommends $DEB libasound2t64 >/dev/null 2>&1
           || apt-get install -y -qq --no-install-recommends $DEB libasound2 >/dev/null; }" ;;
  fedora:*)
    SETUP="dnf -y -q install --setopt=install_weak_deps=False xorg-x11-server-Xvfb xorg-x11-xauth mesa-libGL
      mesa-libEGL mesa-libgbm libdrm libX11 libX11-xcb libxcb fontconfig freetype harfbuzz alsa-lib
      dejavu-sans-fonts which util-linux shadow-utils >/dev/null" ;;
  archlinux:*)
    SETUP="pacman -Sy --noconfirm --needed -q xorg-server-xvfb xorg-xauth mesa libdrm libx11 libxcb fontconfig
      freetype2 harfbuzz alsa-lib ttf-dejavu which >/dev/null" ;;
  *) echo "No setup known for $IMAGE"; exit 1 ;;
esac
SETUP="$(echo "$SETUP" | tr -s ' \n' ' ')"

if [[ "$APP" == *.deb ]]; then
  INSTALL="apt-get install -y -qq /tmp/HomeDocs.deb >/dev/null"  # as root, like the software installer
  MOUNT=(-v "$APP:/tmp/HomeDocs.deb:ro")
  PREP="true"
  CMD="homedocs"
else  # from a copy, as people run it; it extracts itself (containers have no FUSE)
  INSTALL="true"
  MOUNT=(-v "$APP:/HomeDocs.AppImage:ro")
  PREP="cp /HomeDocs.AppImage . && chmod +x HomeDocs.AppImage"
  CMD="env APPIMAGE_EXTRACT_AND_RUN=1 ./HomeDocs.AppImage"
fi

MOUNT_MODELS=()
if [ -n "$MODELS" ]; then
  MOUNT_MODELS=(-v "$(readlink -f "$MODELS"):/models:ro")
fi

# Runs as an ordinary user, the way people run it.
"$ENGINE" run --rm "${MOUNT[@]}" "${MOUNT_MODELS[@]}" -v "$TINY:/selftest.gguf:ro" "$IMAGE" bash -c "
  set -e
  $SETUP
  $INSTALL
  useradd -m tester
  su tester -c '
    set -e
    cd ~
    mkdir -p data/models
    [ -d /models ] && cp -r /models/. data/models/
    cp /selftest.gguf data/models/selftest.gguf
    $PREP
    echo \"== $IMAGE (glibc \$(ldd --version | head -1 | grep -o \"[0-9.]*\$\"))\"
    HOMEDOCS_HOME=~/data timeout 180 \
      xvfb-run -a $CMD --self-test 2>&1 | tee log.txt | grep -E \"SELF-TEST|rror\" || true
    grep -q \"SELF-TEST OK\" log.txt || { echo \"--- full log\"; cat log.txt; exit 1; }
  '
"
