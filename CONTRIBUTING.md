# Working on HomeDocs

How the code fits together: **[ARCHITECTURE.md](ARCHITECTURE.md)**. What's in scope: **[REQUIREMENTS.md](REQUIREMENTS.md)**.

## One-time setup

```bash
pipx install pre-commit   # or: pip install --user pre-commit
pre-commit install        # adds the commit and push hooks to this clone
```

From then on:

- **every `git commit`** runs `ruff` on what's being committed: it formats the code and fixes what it safely can.
  If it changed anything the commit stops; `git add` the changes and commit again.
- **every `git push`** checks everything (lint, formatting, line length, a few file checks) without changing
  anything. A push with problems is stopped before it leaves your computer. CI runs the same checks.

Run the push checks any time with `pre-commit run --all-files --hook-stage pre-push`.

## Running and testing

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m homedocs.ui        # the desktop app (or: python homedocs_app.py)
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

`HOMEDOCS_HOME=/some/folder` points the app (and the tests) at a separate data folder, so you can try things
without touching your own.

**Answers** need llama.cpp, which isn't on pip: build it once (a few minutes; needs git and a C/C++ compiler,
and fetches CMake if it's missing) with

```bash
bash packaging/build_llama.sh          # into build/llama, where the app looks when run from source
```

Without it the app works as before and shows passages without an answer. The tests use a fake chat model either
way; `tests/test_llm.py` runs our bindings against a real one when you point it at a small GGUF file:
`HOMEDOCS_TEST_GGUF=/path/to/Qwen3-0.6B-Q4_K_M.gguf`.

## Dependencies

Kept to the minimum (see ARCHITECTURE.md, *Dependencies*). Before adding a library, check what it pulls in
with it and how much of it would be used. Never add a big library for one small feature: use the standard
library, or a small one made for that job. Every pin in `requirements.txt` gets a comment saying why.

## Tuning

Constants go at the top of `homedocs/core/config.py`, one per line, each with a comment. (Passage size, search
candidate counts and the like arrive with indexing and search.)

## The icon

The logo is drawn in code (`theme.brand_mark`). After changing it, update `homedocs-icon.svg` to match and run
`QT_QPA_PLATFORM=offscreen .venv/bin/python packaging/make_icons.py` to remake the .png, .ico and .icns.

## Branches

- **`main`** always works. Nobody pushes to it directly; changes arrive through pull requests that
  passed CI.
- **Work happens on short-lived branches** cut from `main`, named for what they do: `feature/<name>`,
  `fix/<name>`, `chore/<name>`, `docs/<name>`.
- Open a pull request into `main` and turn on GitHub's auto-merge: `gh pr merge <number> --auto --squash`. Once
  every required check passes it's **squash-merged** (one commit per change on `main`). To hold it back, don't turn
  auto-merge on, or turn it off with `gh pr merge <number> --disable-auto`.
- `main` is protected: changes only arrive through pull requests, and `lint`, `test`, `test-windows`, `linux`
  and `windows` must all pass. Force-pushes and deleting `main` are blocked.

## CI/CD

| Workflow | Runs on | Does |
|---|---|---|
| `CI` (`.github/workflows/ci.yml`) | every push, every branch | lint (same checks as the pre-push hook) and tests on Linux, Windows and Mac; required for merging into `main` |
| `Build HomeDocs` (`.github/workflows/build.yml`) | every pull request; a merge into `main` with a new version; by hand from the Actions tab | builds the AppImage and .deb in AlmaLinux 9 and self-tests them on six distros, the Windows installer and the Mac disk image. A pull request's build saves no files on GitHub; a new version's publishes a GitHub Release with all of them and then deletes its copies; one started by hand keeps them, to download |

## Releasing

Versions follow `MAJOR.MINOR.PATCH`. Bump `__version__` in `homedocs/__init__.py` (the only place it's
written) in the pull request that ships the change. When it merges, the build workflow sees a version with no
release yet, builds everything from `main` and publishes the release, tagged `v<version>`, with the Windows
installer, Mac disk image, AppImage and .deb attached. No one pushes tags by hand. A merge that leaves the
version alone releases nothing.
