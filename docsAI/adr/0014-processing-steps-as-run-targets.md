# ADR 0014: Processing steps as run targets

Date: 2026-10-15 - Status: accepted

## Context

The first version of the GUI had no step tick boxes: every run wrote every file, and the lineage cache made sure only changed
parts were computed (ADR 0007 rejected manual step selection of the legacy GUI as error-prone). Steps are
wanted back: choosing "download the source data", "heightmap", "climate mod" ... makes the workflow
transparent, and a run writes only what was asked for.

## Decision

- **Engine:** `Engine.run(..., targets=...)` - optional node ids; then only these nodes and their producers run (still
  stopping at cached results), and no graph outputs are returned. Without targets nothing changes. The engine stays
  free of domain knowledge.
- **Steps are data:** `configs/gui.toml [[steps]]` - key, label, description, `nodes` (path patterns, sinks only) and
  `types` (node type patterns, any node). The first step that matches a sink owns it; a sink of no step runs in every
  run. `app/steps.py`: `ProcessingStep`, `StepPlan` (assignment for one plan, targets for a choice).
- **Dependencies stay automatic.** A step computes whatever it needs - "Heightmap" runs the whole terrain chain,
  including the town and industry planning the settling needs - but writes only its own files. The cache makes a
  later step cheap.
- **"Copy the files into the game"** is a separate tick box: off = no copy destinations for the run.
- The page remembers the choice in `local/gui_state.json`.

Steps of the built-in configuration: download the source data (types `elevation.read*`, `osm.*`,
`landcover.worldcover`), heightmap, biome map and import layers, towns and industries, climate mod, map mods, previews
and plans. A unit test checks that every sink of the map graphs belongs to a step.

## Alternatives

- **Steps as subgraphs or graph variants** - would split the map graph; a step is a view on the sinks, not a different
  composition.
- **Skipping nodes by step without their producers** - the user would have to know the dependencies (the reason ADR
  0007 rejected the legacy tick boxes).
- **Steps hard-coded in the GUI** - a new sink or graph would need GUI code.

## Consequences

ADR 0007 stays valid: what is computed is still decided by lineage and cache; the tick boxes only choose what is
written. A new sink must match a step pattern (else it runs in every run, and the unit test fails for the built-in
graphs). The CLI still writes everything; a `--steps` option would use the same `StepPlan`.
