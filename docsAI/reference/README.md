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
- Two path rules: `extends` is relative to the file that contains it (`"../presets/alpine_realistic.toml"` in a
  project); paths inside values (`dem_path`, pictures) are relative to the program folder.
- **Switching off an inherited part:** `enabled = false` in a table of `[defaults.climate]` removes it, e.g. a patch
  `[defaults.climate.surfaces.alpine_ground.look.patches.ground_scree] enabled = false` (works for every table of the
  climate preset; `enabled = true` is dropped). A list or single value is simply replaced.

## Typical tasks

**Change the look of one map** (e.g. tree line): in the project, `[defaults.climate.heights]` with only the changed
value. Types: `ClimatePreset` -> `HeightLimits` in [nodes.md](nodes.md#heightlimits). **Also set an own
`[defaults.climate] name`** (e.g. `"Alpine Brown"`): the climate mod is named after the preset name, the reference
height rounded to 100 m and the scale, and maps with the same name share one mod - without an own name the changed
look overwrites the mod of other maps.

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

**Symptom -> setting** (project file; node paths of the graph `map`; defaults in [nodes.md](nodes.md)):

| The map maker says | Change | Note |
|---|---|---|
| too little real water, rivers only painted | `[params] real_water_until_m` (default: lowest point + 120 m); `[nodes."terraforming/rivers_and_water/rivers"] min_real_run_game_m` | real water is cut down to the game's single water level (game height 0): a river at 750 m on a map with lowest point 549 m lies in a trench about 200 m (real) deep. Raise in small steps, check `_rivers_preview.png` and `_rivers_profiles.png` |
| flat land: every brook is real water in a trench | preset *Lowland* (`lowland_realistic`) or `[params] real_water_rivers = "navigable"`; `navigable_names = ["<canal name>"]` for waterways without OSM `CEMT` class; `ramp_length_game_m` (e.g. 500) | only navigable waterways become real water, other rivers stay painted; the ramp lowers painted riverbeds to the water line where they meet real water. Check `_rivers_preview.png` (navigable real water purple) |
| rivers too narrow | `[nodes."terraforming/rivers_and_water/rivers"]` `min_width_game_m` (real water), `painted_width_game_m` (painted, [min, max]) | real water widths follow WorldCover up to `max_width_game_m` |
| too many / too few towns | `[params] max_towns`, `town_min_distance_game_m` (default 3500 game m - few towns on small maps); `[nodes."planning/towns/towns"] towns_per_100_km2` | `example.md` step 7 |
| too few / too many industries; a cargo chain missing (e.g. no ore on flat land) | `[params] industry_density` (default 1); `all_cargos = true`; resource heights: `[defaults.industries.sites.resources.<key>] height_m` | `all_cargos` ignores land cover and height (unrealistic, not fish); off: factories without input source are skipped when there are no trade points. Check `_resources_preview.png` |
| forest too high / too low | `[defaults.climate.heights] tree_line`, `trees_dense_until`, `trees_sparse` | real metres a.s.l. |
| rock, scree or snow too low / too high | `[defaults.climate.heights] rock_start/full`, `scree_start/full`, `snow_start/full`, `snow_hollow_start/full` | these rules paint over the surfaces below them |
| another ground texture in a zone (e.g. brown grass in the high mountains) | `[defaults.climate.surfaces.<surface>.look] base = { kind = "game", name = "<material>" }`, patches off with `enabled = false` | zones: table below; material names: list below; own `name` (see above) |
| biome borders (valley / mountain forest / subalpine / alpine) | `[defaults] biome_limits_m`, `mountains_from_m` | effective height = height + steepness bonus + noise |

**Zones of the map** (temperate base climate, preset *Alpine Realistic*):

| Zone | Where | Surface in `[defaults.climate.surfaces]` |
|---|---|---|
| valley and mountain forest land | biomes 1 and 2 (effective height below 1500 m) | `meadow` (meadow layer x lowland), `field`, `crop_*` |
| high meadows | biomes 3 and 4 (effective height above 1500 m), game area `alpine_grass` | `alpine_grass` |
| high mountains | biome 4 x mountain mask (effective height above 1800 m), game area `alpine_ground` | `alpine_ground` |
| rock and scree | rock layer (WorldCover bare ground) and steep slopes; above `rock_start` / `scree_start` by height | `rock` |
| snow | above `snow_start` / `snow_hollow_start` | height rules only (`[defaults.climate.heights]`) |
| water, shore, river bed, waterfalls | layers `water`, `shore`, `riverbed`; steep painted water | `water`, `shore`, `riverbed`, `waterfall` |

**Game materials** (`kind = "game"`; the material folders of TF3, `base/content/terrain/materials/`, read 2026-10-18;
`check` reports an unknown name when the game folder is set in `local/settings.toml`):
`asphalt_01`..`06`, `ballast`, `clf_rck_01`, `clf_rck_02`, `cliff_canyon_01`..`04`, `cliff_mesa_01`, `coal`,
`coal_dust_01`, `corn`, `dirt`, `dirt_country_road`, `dirt_dry`, `forest_ground`, `forest_ground_dry`, `gr_br_sub`,
`gr_sub_cst`, `gr_tr_01`..`03`, `grass_alpine`, `grass_alpine_tr`, `grass_brown`, `grass_cutted_01`,
`grass_cutted_01_field`, `grass_cutted_02`, `grass_dark_green`, `grass_dry_01`, `grass_gravel`, `grass_gravel_dry`,
`grass_gravel_dry_02`, `grass_green`, `grass_green_tr`, `grass_light_green`, `grass_light_green_tr`, `gravel_01`..`04`,
`gravel_dry_01`, `iore_dust_01`, `lva_rck_01`, `msy_grnd_01`, `msy_grnd_02`, `msy_grnd_04`..`07`, `msy_rck_01`,
`msy_rck_02`, `qry_rck_02`, `river_bed`, `rock`, `rock_dry`, `rock_sub_01`, `sand_01`..`07`, `sand_red_01`,
`sand_red_02`, `sand_yellow`, `scree`, `scree_dry`, `scree_dry_02`, `scree_sub_01`, `scree_sub_02`, `snow_01`,
`soil_01`, `soil_sand_01`..`03`, `sw_soil_01`, `tiles_hexagon`, `water_dirty`, `wheat`.

**Another processing step** (replace a node, add a mask): an own graph in `configs/graphs/` that `extends` the group
graph and replaces or removes nodes, plus a map graph that `extends` `map` and uses the new group; format in
[node_guide.md](../../docsHuman/node_guide.md#2-graph-documents-idgraphjson), example: `map_parcels` and
`map_terraforming_parcels` in [graphs.md](graphs.md). To offer it in the GUI, add it to `graphs` in
`configs/gui.toml`. Graphs can only wire existing node types; a new kind of processing needs a new node (code).

## Check every change

```
AutoMapGen.exe check --project <map>      (in the program folder; source checkout: automapgen check --project <map>)
```

lists every problem (unknown names, wrong types, values out of limits, broken links, game materials the game does
not have) without running anything, and warns when the project changes the look without an own climate name; the GUI
shows the same under *Graph*. Then generate the map; with the cache only the affected steps run. Keep a
copy of a file before changing it; files written by the GUI lose their comments.
