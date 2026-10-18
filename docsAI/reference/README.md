# Reference for AI assistants: changing presets, projects and graphs

For an AI assistant that helps a map maker change tf3-AutoMapGen without code. Give the assistant this file, the file
to change and, as needed, [nodes.md](nodes.md) (every node type and parameter, generated) and
[graphs.md](graphs.md) (every built-in graph, generated). Background: [glossary](../glossary.md), formats in depth:
[node_guide.md](../../docsHuman/node_guide.md) sections 2-3, the look: [climate_mod.md](../../docsHuman/climate_mod.md).

## What lives where

All paths are relative to the program folder (the folder of `AutoMapGen.exe`, or the repository root).

| File | What it is | Change it for |
|---|---|---|
| `configs/projects/<map>.toml` | one map: `graph`, `extends`, `[params]` (region, size, scale, heights, towns), single node settings | this map only |
| `configs/presets/<preset>.toml` | a look: `[defaults]` (biome limits, land cover classes of the layers, crops, `climate` = the climate mod's look) | every map with this look |
| `configs/landscapes/<landscape>.toml` | a preset + `transition`: a second look over part of the map | a gradual change of look |
| `configs/industries/`, `mod_texts/`, `publishing.toml`, `waterfall.toml`, `map_mods.toml` | values a preset extends | industries, texts, names, waterfall brushes, the map's own mods |
| `configs/graphs/<id>.graph.json` | own graphs (searched before the built-in ones) | another processing |
| `configs/gui.toml` | GUI: graph list, processing steps, grouping of the advanced settings | what the GUI offers |
| `local/settings.toml` | game folder, user data folder, API key | set in the GUI, not by hand |

The built-in graphs are inside the program (`src/automapgen/graphs/`, in the build `_internal/`); do not change them,
make an own graph that `extends` one.

## How values are combined

- A project `extends` a preset or landscape, which `extends` further files. Later files win; **tables merge key by
  key**, lists and single values are replaced. So a file names only what differs, down to one value in a table.
- `[params]` (projects) and `[defaults]` (presets) feed the **exposed parameters** of the graph (list per graph in
  [graphs.md](graphs.md)). `[defaults]` values a graph does not expose are ignored; unknown `[params]` are errors.
- `[nodes."<path>"]` sets parameters of one node. The path goes through the subgraph nodes, e.g.
  `terraforming/draft_terrain/draft/water` = node `terraforming` of `map` -> `draft_terrain` -> `draft` -> `water`
  (follow the `Nodes:` lists in [graphs.md](graphs.md)). Parameter names and types: [nodes.md](nodes.md).
- Order for a node parameter: node default < graph document < `[defaults]` < exposed parameter < `[nodes."<path>"]`.
- Units: `_m` = real metres (a.s.l. for heights), `_game_m` = game metres (real / scale), `_km` = game km for sizes.
  Paths inside values are relative to the program folder.

## Typical tasks

**Change the look of one map** (e.g. tree line): in the project, `[defaults.climate.heights]` with only the changed
value. Types: `ClimatePreset` -> `HeightLimits` in [nodes.md](nodes.md#heightlimits).

**A new look for several maps:** copy `configs/presets/alpine_realistic.toml` to a new name and change
`[defaults.climate] name` (it names the climate mod) and the values; or make a small preset that `extends` the
original and changes only the differences. Projects then `extends` the new file; the GUI lists it under *Climate
preset* automatically.

**A gradual change across the map:** a landscape like `configs/landscapes/brenner_north_south.toml`
(`[defaults.transition]`: zone, mask layer slot, mix, factors per surface; type `Transition` in
[nodes.md](nodes.md)).

**Towns, industries, rivers, fields:** find the node in [graphs.md](graphs.md) (e.g. `map` -> `planning` ->
`towns` -> `towns`, type `towns.plan`), read its parameters in [nodes.md](nodes.md), set them under
`[nodes."planning/towns/towns"]` in the project, e.g. `towns_per_100_km2 = 1.5`.

**Another processing step** (replace a node, add a mask): an own graph in `configs/graphs/` that `extends` the group
graph and replaces or removes nodes, plus a map graph that `extends` `map` and uses the new group; format in
[node_guide.md](../../docsHuman/node_guide.md#2-graph-documents-idgraphjson), example: `map_parcels` and
`map_terraforming_parcels` in [graphs.md](graphs.md). To offer it in the GUI, add it to `graphs` in
`configs/gui.toml`. Graphs can only wire existing node types; a new kind of processing needs a new node (code).

## Check every change

```
AutoMapGen.exe check --project <map>      (source checkout: automapgen check --project <map>)
```

lists every problem (unknown names, wrong types, values out of limits, broken links) without running anything; the
GUI shows the same problems under *Graph*. Then generate the map; with the cache only the affected steps run. Keep a
copy of a file before changing it; files written by the GUI lose their comments.
