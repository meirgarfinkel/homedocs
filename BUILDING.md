# Packaging HomeDocs

This turns HomeDocs into something you can hand to anyone:

- **Linux:** `HomeDocs-x86_64.AppImage`, a single file. Make it executable once, then double-click.
- **Ubuntu, Debian, Mint, Pop!_OS:** `HomeDocs.deb`, the same app as a package. Double-click it, and the software installer installs it.
- **Windows:** `HomeDocsSetup.exe`, a normal installer with Start menu and desktop shortcuts.
- **Mac:** `HomeDocs.dmg`, a disk image holding `HomeDocs.app` to drag into Applications.

Each contains its own copy of Python and every library, llama.cpp included, so the people you give them to
install nothing else. The models are never bundled: they download once, on first run.

Every build compiles llama.cpp first (`packaging/build_llama.sh`, or `build_llama.bat` on Windows), from the
release pinned in `build_llama.sh`, together with `native/hd_llama.c`. It takes a few minutes the first time
and seconds after. Its CPU code is built once per CPU generation and the best one is picked when the app
starts, so one bundle runs on old and new processors alike. Each build's self-test then writes a few words with
the 1 MB story model in `tests/fixtures`, which shows llama.cpp loads and runs on that system.

A bundle can only be built on the system it's for: the AppImage on Linux, the installer on Windows, the disk
image on a Mac. If you don't have a Windows PC or a Mac, GitHub builds them all for you (see *Let GitHub build
them*).

---

## Linux AppImage

The AppImage runs on the Linux it was built on and on newer ones. Release builds are therefore made inside
**AlmaLinux 9** (glibc 2.34), whatever Linux your own computer runs. The result runs on Debian 12+, Ubuntu
22.04+ (and Pop!_OS, Mint 21+), Fedora, Arch, and other distros of similar age or newer. It is x86_64 only.

1. **Install Docker or Podman** (one time). On Pop!_OS/Ubuntu: `sudo apt install podman`.

2. **Build**, from the `homedocs` folder:
   ```bash
   bash packaging/build_in_container.sh
   ```
   The first time takes several minutes, because it sets up the AlmaLinux build machine. Later builds are
   quicker. When it finishes it prints `Done: .../HomeDocs-x86_64.AppImage`. The build stops with an error if
   any bundled library would need a newer system than AlmaLinux 9.

3. **Check it works:**
   ```bash
   ./HomeDocs-x86_64.AppImage --self-test
   ```
   It should print `SELF-TEST OK`. After that, double-click the AppImage to use it normally. The first time it
   runs, it adds **HomeDocs** to your applications menu.

4. **Optional: check it on other distros.** This runs the self-test in a fresh Debian, Ubuntu, Fedora or Arch
   container:
   ```bash
   bash packaging/smoke_test.sh debian:12 HomeDocs-x86_64.AppImage
   ```
   GitHub runs this on six distros for every pull request (see below).

**Quicker builds while trying things:** `bash packaging/build_linux.sh` builds directly on your computer,
without a container (it needs `sudo apt install python3-venv libxcb-cursor0 build-essential git`). That AppImage may not run on
systems older than yours.

**What's left to the computer it runs on:** graphics libraries (they must match its drivers), fonts, sound,
and the C/C++ runtime. Every desktop Linux has these. Everything else is bundled.

If double-clicking does nothing on some computer, running it as
`./HomeDocs-x86_64.AppImage --appimage-extract-and-run` works without the FUSE support AppImages normally use.

**The .deb:** `bash packaging/build_deb.sh` turns the AppImage into `HomeDocs.deb` (it needs `dpkg-deb`, on
every Debian or Ubuntu). It's the same app, installed in `/opt/homedocs`, with `homedocs` to start it, a menu
entry and the icon. Its version comes from `homedocs/__init__.py`. To check it installs and runs on a fresh
system: `bash packaging/smoke_test.sh ubuntu:24.04 HomeDocs.deb`.

---

## Windows installer (on a Windows 10 or 11 PC)

1. **Install Python 3.11 or 3.12** from [python.org](https://www.python.org/downloads/). In the installer, tick
   **Add python.exe to PATH**.
2. **Install Inno Setup 6** from [jrsoftware.org](https://jrsoftware.org/isinfo.php).
3. **Install Visual Studio** (the free Community edition) with **Desktop development with C++**, and
   [Git](https://git-scm.com/download/win), to compile llama.cpp. Tick **C++ Clang tools for Windows** in
   that workload too: llama.cpp writes answers faster built with clang (without it, MSVC is used).
4. **Open the `homedocs` folder** in File Explorer, type `cmd` in the address bar and press Enter.
5. **Build:** `packaging\build_windows.bat`. It sets up Python, compiles llama.cpp, bundles the app, runs a quick self-test (with a
   data folder of its own, `build\selftest`, never yours) and makes the installer. It finishes with
   `Done: HomeDocsSetup.exe`.
6. **Share `HomeDocsSetup.exe`.** It installs HomeDocs for that user (no administrator password needed).

**The "Windows protected your PC" warning:** the installer isn't digitally signed, so people click
**More info**, then **Run anyway** the first time.

---

## Mac disk image (on a Mac)

1. **Install Python 3.12** from [python.org](https://www.python.org/downloads/macos/) (or `brew install python@3.12`),
   and Apple's command line tools (`xcode-select --install`) to compile llama.cpp.
2. **Build**, from the `homedocs` folder in Terminal: `PYTHON=python3.12 bash packaging/build_mac.sh`. It sets
   up Python, compiles llama.cpp, bundles the app, signs it ad hoc, runs a quick self-test (with its own data folder,
   `build/selftest`) and makes the disk image.
3. **Share `HomeDocs.dmg`.** People drag HomeDocs into Applications, then allow it once in System Settings >
   Privacy & Security > **Open Anyway** (the app is signed only "ad hoc", with no Apple Developer account).

The app is built for Apple Silicon (M1 or newer) and needs macOS 14 or newer.

---

## Let GitHub build them

`.github/workflows/build.yml` builds on GitHub's computers:

- **Every pull request** builds the AppImage in AlmaLinux 9 and runs its self-test on Debian 12 and 13,
  Ubuntu 22.04 and 24.04, Fedora and Arch. It makes `HomeDocs.deb`, installs it on Debian 12, Ubuntu 22.04 and
  24.04, and runs the installed app's self-test. It also builds the Windows installer and the Mac disk image and
  runs each one's self-test.
- **By hand** (Actions tab > **Build HomeDocs** > **Run workflow**): the same, and the files are kept to
  download under **Artifacts**.
- **A merge into main with a new version** (`__version__` in `homedocs/__init__.py`) does everything and
  publishes a GitHub Release, tagged `v<version>`, with all four files.

---

## If something goes wrong

- Run the app with `--self-test`. It says which part failed.
- The app writes a log to `~/.local/share/homedocs/app.log` (Windows: `%LOCALAPPDATA%\homedocs\app.log`;
  Mac: `~/Library/Application Support/homedocs/app.log`).
