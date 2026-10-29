# ADR 0019: Heights and distances - two factors

Date: 2026-10-26 - Status: accepted

## Context

One factor (1 / `scale`) converted both real distances and real heights to game metres. Map makers want maps with
real (or less flattened, or exaggerated) heights while the region still shrinks by the scale. Code mixed the factors
freely (e.g. the lake report converted depths with `frame.horizontal_scale`, a distance factor).

## Decision

- Project / `map.format` parameter `height_scale` (None = `scale`): heights 1:height_scale, distances 1:scale.
- `MapFormat.game_per_real` = distances (lengths, widths, radii in game metres).
- `MapFormat.height_game_per_real` = heights: elevations, depths, sinks, and slopes expressed as height per pixel.
- `MapFrame.horizontal_scale` only for pixel widths of the source data.
- Slopes in game get scale / height_scale times steeper; `map.format` logs this when the factors differ.
- The climate mod name adds the height factor when it differs from the scale (`ClimateNaming`).

## Alternatives

- Separate data type for the height factor: one more input on every node that needs both; the map format already
  travels to these nodes.
- Boolean `real_heights` (first version on this branch): only 1:1 or 1:scale, no "less flattened" or exaggerated maps.

## Consequences

- Nodes that convert heights take `map_format` as input (e.g. `water.shape`, `roads.terrain`, `riverbed.groove`).
- New code must choose the factor explicitly; rule in `docsHuman/node_guide.md` section 1.
- `MapFormat.from_json` falls back to `scale` for cached values without `height_scale`.
