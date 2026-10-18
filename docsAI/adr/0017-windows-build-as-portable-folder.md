# ADR 0017: Windows build as a portable folder

Date: 2026-10-18 - Status: accepted

## Context

The prototype release should run without Python. Everything the program reads and writes is
relative to the working folder: `configs/` (presets, landscapes, projects), `assets/`, `local/` (settings, key),
`downloads/`, `cache/`, `output/`. Map makers are meant to change presets and graphs, also with an AI assistant, so
the configs must stay visible and editable.

## Decision

- **PyInstaller, one folder** (`AutoMapGen.exe` + `_internal/`), zipped (not a single exe: slow
  start, more virus-scanner alarms). `packaging/automapgen.spec`, `packaging/build.py`.
- **Portable:** the folder of the exe is the working folder (`app/portable.py`, `PortableStart`). Configs, assets,
  schemas and docsHuman lie next to the exe; projects, downloads, cache, output and `local/` are created there. A
  double click starts the GUI; with arguments the exe is the CLI.
- **Read-only folder** (e.g. below `C:\Program Files`): clear message to unpack elsewhere; the console stays open on
  an error after a double click.
- **Licences:** `build.py` copies the licence files of every runtime dependency (recursively) into `licenses/`.
- Only the example project `brenner.toml` is shipped; other projects in `configs/projects/` are test projects.
- The GUI edits the game folder and the user data folder (`local/settings.toml`), so no file has to be written by
  hand.

## Alternatives

- User folder (`Documents\tf3-AutoMapGen`) apart from the program: safe below `Program Files`, but configs in two
  places (shipped read-only + copies) and an update/merge question. Rejected for the prototype.
- Installer (Inno Setup, MSIX): more work, signing; later if the tool grows.
- Embedded Python + `pip install`: no frozen-import problems, but a start script and network at install time.

## Consequences

- A new version is a new folder; the user copies `configs/projects/`, `local/` and optionally `downloads/` over.
- Node code hashes (ADR 0007) fall back to the qualified name in the build (no source); the cache of one build folder
  is not invalidated by code changes - harmless, since every version is a new folder.
- The exe is not signed: Windows SmartScreen warns on the first start.
