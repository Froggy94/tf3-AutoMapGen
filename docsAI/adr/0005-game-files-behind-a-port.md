# ADR 0005: Original game files behind a source port

Date: 2026-10-12 - Status: accepted

## Context

The climate mod is built from the game's original climate scripts. They are not ours to publish, so they are read
from the game installation (or a local copy for development), and switching between the two must not need
restructuring.

## Decision

- Original files are never committed (`/game_originals/` is ignored), so the history needs no cleaning before release.
- Nodes never read them directly: a source port `GameFiles` supplies them, implemented by a local copy now and by the
  game installation later - switching is a setting.
- Tests do not need the originals; they use small own sample files.

## Consequences

CI runs without game files. The port also decouples nodes from game updates: file locations are known in one place.
