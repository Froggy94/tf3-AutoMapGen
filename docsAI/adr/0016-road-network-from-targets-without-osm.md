# ADR 0016: Road network from targets, without OSM first

Date: 2026-10-17 - Status: accepted; refines ADR 0012 (order, open points decided)

## Context

ADR 0012 planned a hybrid: OSM roads as a corridor, our route search fits the road into the terrain. Open were the
terrain under the roads (game or heightmap) and who plans the heights. TPF3 Map Studio (MIT,
github.com/sharpi13822/TPF3-Map-Studio, analysed 2026-10-15/17) builds OSM networks in game with junctions, bridges
and tunnels (*verified there*, October 2026, not by us); it has no minimum radius, its heights come from the mod.

## Decision

- **Targets first, without OSM:** roads connect our planned towns and industries (origin-destination); the route
  search alone fits them into the terrain. The OSM corridor of ADR 0012 comes later as an extra cost term.
- **Bridges and tunnels from the terrain:** where the grade-limited profile lies far above the ground it is a bridge,
  far below a tunnel - no OSM tags needed.
- **Heights planned by us:** the plan gives every node its game height; the mod builds with them (no smoothing in
  game). A constant offset between plan and game terrain (minimum height entered by hand at import) is measured on
  the normal edges and corrected.
- **Terrain under the roads in the heightmap,** stamped like the OSM roads (`roads.terrain`): level across, the
  profile along, banks; bridges and tunnels leave the terrain alone. The game then needs almost no cut and fill.
- **Standard street types.**
- **Building in game after Map Studio's mechanics:** proposals with given positions, edge type bridge/tunnel with a
  type from `bridgeTypeRep`/`tunnelTypeRep`, junctions by using an existing node entity (found by position in
  `streetSystem.getNode2SegmentMap()`); our update loop and start signal stay (they built the old network).

## Steps

| Step | Content |
|---|---|
| S1 | plan format with nodes (u, v, game height) and ways (kind normal/bridge/tunnel), shared nodes = junctions; road mod builds it (heights, structures, junctions, height offset) |
| S2 | targets: planned towns and industries instead of OSM places |
| S3 | route search: heading-aware grid (minimum radius in game metres), soft grade cost, water and valleys crossable at a cost |
| S4 | longitudinal profile (grade limit), bridges/tunnels from profile vs ground, terrain stamped into the final heights before the paths |
| S5 | graph `map_roads` wired, previews, Brenner run; game test |

## Implementation (2026-10-18)

- Package `nodes/roads/`: `coarse.py` (100 m grid of the whole map, mean values, serpentines allowed as a longer
  line), `lattice.py` (25 m grid x 16 headings in a corridor of 150 m around the coarse route, doubled twice if no
  route fits: coarse first, deviate only if needed), `alignment.py`, `profile.py`,
  `network.py` (node `roads.network`), `plan.py`.
- Graph `map_roads` = map with `final_terrain` -> `map_final_terrain_roads`: the network is planned on the settled
  terrain with water, shaped into it, the OSM roads and paths keep off it. `map_parcels` extends `map` now (no road
  network; farms paused); the comparison runs `map` and `map_parcels`.
- Brenner (16.4 x 49.2 km, 16 towns, 34 industries): fine search alone 442 s, coarse to fine 81 s; 4 links fail in
  the corridor (open).
- Parked alternative: the game's own builder `api.cmd.makeTownConnectWithIndustriesCmd`
  (`api/tealdef/api/cmd.d.tl`), fast but blind for painted water and riverbeds.

## Alternatives

- Heights smoothed in the mod (Map Studio): bridges and tunnels need the profile beforehand, so the plan has it.
- The game's own cut and fill: the heightmap would differ from the planned road; banks look better when planned.

## Consequences

- The road network is planned inside the final terrain (on the settled terrain with water), before the OSM roads
  and paths are stamped; those keep off the planned roads.
- Editing towns/industries in the editor then needs a new heightmap, not only a new road mod.
