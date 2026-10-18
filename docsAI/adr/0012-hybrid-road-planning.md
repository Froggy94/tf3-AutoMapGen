# ADR 0012: Hybrid road planning - OSM corridor, drivable route search

Date: 2026-10-14 - Status: accepted (terrain under the roads: open until the game test)

## Context

The planned road network (`roads.network`) is built in game, but looks bad (Brenner map): curves with a radius
below half the road width fold over (87 places below 8 m), the road follows every bump of the ground (18 % of the
course steeper than 12 %, up to 75 %), there are no bridges or tunnels, junctions are gaps. The route search works on
a 25 m grid with a grade limit per cell, smooths the corners slightly and has no longitudinal profile. OSM roads are
only worked into the terrain as terraces, independent of the built network.

Taking the OSM roads as they are works well only at scale 1:1 (radii halve at 1:2), leaves the choice of roads open
and does not include industries.

## Decision

Hybrid: OSM gives the rough course, our route search fits the road into the terrain.
- **OSM as a corridor:** the route search is cheaper near OSM roads, weighted by road class (trunk/primary strong,
  secondary medium, tertiary/unclassified weak, no motorways); corridor width in game metres, so the route may leave
  the OSM line as far as radius and grade need at the map's scale. Without an OSM road the search is free.
- **Drivable route search:** straight pieces and arcs with a minimum radius (game metres), grade limited along a
  smoothed longitudinal profile; bridges and tunnels as parts with a cost (cheap where OSM has them).
- **Targets:** places and industries - from our planning or from the map editor's export file (towns and
  industries edited there), the road planning runs separately in the GUI.
- **The game builds:** the script gives the planned heights and the edge kind (normal, bridge, tunnel); on steep
  slopes steep street types and retaining walls of a separate street mod (edge decorations per edge: lower wall on
  fill, upper wall in cuts), so the road mod depends on that mod.
- Order: R0 game test (test mod `road_test`), R1 route search with OSM corridor, minimum radius and profile,
  R2 editor export file as input, R3 bridges and tunnels, R4 junctions.

## Alternatives

- **Improving the grid search only** (minimum radius after the search, coarser grid) - curves better, grades and
  structures still invented without the real road course.
- **The OSM road network as it is** - scale, choice of roads, no industries.

## Consequences

- If the game's own cut and fill looks good with given heights, the heightmap stays independent of the planned
  roads: editing industries needs only a new road mod, no new heightmap import. Otherwise the terrain under the
  roads goes into the heightmap (decided after the test).
- The test mod showed in the game's own track builder that a junction is built by using an existing node entity as
  a node of the new edge (`base/content/mission/tasks/auto_builder/track_builder.tl`).
