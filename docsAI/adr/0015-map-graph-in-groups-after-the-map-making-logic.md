# ADR 0015: The map graph in groups after the map-making logic

Date: 2026-10-16 - Status: accepted

## Context

`map` had 96 nodes and 186 links on its top level; its diagram explained nothing. Wanted: a
structure that follows the human logic of making a map - "as concrete as possible, as abstract as necessary" - and
that answers for each part what it does, where and how. A coarse draft and then a draft computed from the real
links were reviewed and accepted (with more meaningful labels in the diagrams).

## Decision

- `map` consists of seven groups, each its own graph `map_<group>` with `title` and `description` (what / where / how):
  `source_data` (format, frame, all sources), `terraforming` (`elevation`, `biomes`, `rivers_and_water`,
  `draft_terrain`), `planning` (`paths_and_roads`, `towns`, `industries`), `final_terrain`, `biome_creation`,
  `climate` (climate_mod + its sinks), `map_mods`. The nodes and their parameters are unchanged; only the composition
  changed (generated from the flat graph by a one-off script: every link across a group border became a named port on
  each level, every exposed parameter is passed down).
- Three deviations from the coarse draft, forced by the data: the map frame is in `source_data` (all sources need
  it); biomes 1-4 are computed in `terraforming` (the planners need them before the final terrain; otherwise the groups
  would form a cycle); `climate_mod` stays flat inside (texturing and vegetation are one node each).
- Graph documents get an optional `title`; the Mermaid diagram shows subgraphs with their title and draws one arrow
  per pair of nodes with all ports on it (the top level of `map`: 14 arrows instead of 60).
- Variants follow the groups: `map_roads` adds the group `road_network` (`map_road_network`) and replaces `map_mods`
  by `map_mods_roads`; `map_parcels` replaces `terraforming` and `final_terrain` by parcel variants
  (`map_terraforming_parcels` -> `map_terraforming_draft_parcels`, `map_final_terrain_parcels`).

## Alternatives

- **Keep the flat graph and only draw groups** (diagram clusters) - the structure would exist only in the picture,
  not in the graphs, parameters and node paths.
- **Biome creation as one group before planning** - would need the final layers before the planning: a cycle.
- **Splitting climate_mod into texturing / vegetation subgraphs** - one node each; the description explains them.

## Consequences

Node paths are longer and go through the groups (`draft/water` -> `terraforming/draft_terrain/draft/water`); the
project files were migrated, the GUI step patterns start with `*/`. Results are byte-equal (comparison tests). A new
variant of one step replaces one node in a group graph and uses that group in a map graph that extends `map`. The
group graphs are written by `GraphFile` (one value per line), so they are longer as text; the diagrams are the
readable view.
