# ADR 0008: Copies into the game as named destinations of the run

Date: 2026-10-13 - Status: accepted

## Context

The map editor imports heightmaps, layers and towns/industries files only from the player's user data folder of the
game (`heightmaps/`, `biomes/`, `towns_industries/`), and the game offers mods from `staging_area/` there. The legacy
project copied each result there inside its steps when a user data folder was configured. Which file goes where is a
property of the map (a graph decision); whether and where it is copied is a property of the computer and the run.

## Decision

- The run's `OutputTarget` has named destinations (name -> folder); the graph framework knows only names, no game.
- Sinks have a parameter `copy_to`: after writing, the file (or mod folder) is also copied to that destination - if
  the run has it, else nothing happens. The map graph names the destination of each file.
- `GameUserData` (nodes, next to `GameFiles`) knows the game's folder layout and gives the destinations for a user
  data folder. The CLI takes it from `user_data_folder` in `local/settings.toml` or `--user-data`; `--no-copy`
  copies nothing.

## Alternatives

- **A copy step after the run (application copies output files)** - the application would have to know which output
  file belongs where: domain knowledge outside the graph, and a second list to keep in sync.
- **Own install nodes per file** - doubles every sink in the graph for the same effect.
- **Destination folders as graph parameters** - machine paths in parameter sets, and changing them would look like a
  change of the map.

## Consequences

Graph documents stay machine-independent; a run without user data folder (tests, CI) copies nothing. Sinks always
run, so a run always refreshes the copies.
