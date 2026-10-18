# ADR 0013: Road network as an experimental graph

Date: 2026-10-15 - Status: accepted

## Context

The first public release is a prototype (technology study) of the current state without roads. The
road network taken over from the legacy project (`roads.network`, `mod.roads`: country roads between places, built
by a game script without junctions) is being redesigned (ADR 0012, ADR 0016). Its
code, tests and notes shall stay - as a reference and as the base of the redesign - but neither the active pipeline
nor the GUI shall offer it.

## Decision

- The standard graph `map` no longer contains the road network: no `roads.network`, no road mod, no road plan files,
  no parameters `road_min_population` and `road_mod`.
- A graph `map_roads` (`extends: map`) adds exactly these nodes, links and parameters. A project switches the road
  network back on with `graph = "map_roads"`.
- `mod.path_streets` takes the road plan as an optional input; without it, field tracks and hiking paths get no gaps
  near roads (same result as an empty plan).
- What stays in `map`: the country roads from OSM worked into the terrain (`roads.from_osm`, `roads.terrain`) and the
  forest gaps along them - they shape the terrain and build nothing.
- `map_parcels` (experimental, paused farms) and the golden-master project extend/use `map_roads`, so the comparison
  with the legacy project (which builds roads) stays complete.
- The GUI shows what the project's graph contains, so the road settings and the road plan preview disappear there
  without a GUI change of its own; the road plan is no longer listed among the GUI previews.
- `road_test` stays a separate graph, marked experimental.

## Alternatives

- **Switching the road network off by a parameter** (`enabled = false`) - the nodes would stay in the graph and in
  the GUI settings; a graph without them is the cleaner contract and costs no code.
- **Deleting the code** - loses the working reference of the legacy behaviour and the comparison tests.
- **A separate branch** - the code would age unnoticed; here it stays compiled, typed and tested.

## Consequences

Projects with `graph = "map"` no longer produce `_roads_plan.*` or the roads mod. `map_parcels` now includes the road
network (as before the split). When the road planning is redesigned, its new graph extends `map` the same way.
