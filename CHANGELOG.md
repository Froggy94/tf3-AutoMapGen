# Changelog

## Unreleased

- Industries: `industry_density` (default 1) multiplies the factories per type, the raw-material industries per type
  and the resource areas. `all_cargos` (default off) gives resources without fitting land areas anyway, regardless
  of land cover and height (unrealistic; not fish). With it off, processing industries whose inputs have no source
  on the map are no longer placed when the map has no trade points. Both are in *Advanced settings*.
- Industries: alternative production rules of the game are read (food factory: fish or meat, each with sheet metal);
  a factory uses the first rule whose inputs are made on the map, so a map without fishing gets its food factory
  supplied with meat.
- Industries: raw-material industries whose output no planned industry and no town uses are not placed (temperate
  standard buildings: the quarry, whose stone only the cement plant of the industry mod takes).
- Industry preset: each resource has its real height band (`height_m`); *Lowland* allows livestock at all heights.
- Waterway classes: `real_water_rivers = "navigable"` makes only navigable waterways real water (OSM `CEMT` class,
  same name, `navigable_names` prefixes, unnamed canals attached to them); other rivers stay painted water at their
  width, also where a canal crosses them. Default `"all"` = as before. The rivers preview shows navigable real water
  in purple.
- Ramp: `ramp_length_game_m` (default 0 = off) lowers a painted riverbed linearly to the water line where it meets
  real water, the terrain beside it within `ramp_corridor_game_m` (60 game m) follows (node `riverbed.ramp`).
- Preset *Lowland* (`configs/presets/lowland_realistic.toml`) for flat land: navigable waterways only,
  500 game m ramps, forest shrunk by 2 px, a third conifers, no boulders, own mod texts
  (`configs/mod_texts/lowland.toml`).
- Forest: project parameter `forest_shrink_px` (default 0 = off) shrinks the forest layer along its edge (1 px = 4
  game m). The game reads import layers coarsely and as yes/no, so hedgerows and small groves spread over the
  neighbouring fields; shrinking removes narrow strips and keeps large woods.
- Fix: a lake crossed by a real river of the river plan is real water too. Before, a flat canal just above the real
  water limit stayed painted at its own level inside the real river: cliffs and dry trenches along the canal.

## 0.1.1 (2026-10-26)

- Experimental road network (`map_roads`): bridges and tunnels only where the route search plans them, as straight
  spans at a constant grade (at most 6 %) with fitted approaches; bridges from 10 m above the ground (below: fills),
  tunnels from 12 m below and 150 m long (shorter: cuts); bridge costs grow with the height; steady grades instead of
  going up and down (ADR 0018).
- Faster terrain stamping of roads and paths (same result).
- Height scale (project parameter `height_scale`, GUI field *Heights 1:*): heights get their own scale, empty = like
  the distances, 1 = real heights, below 1 = higher than real (ADR 0019). The GUI shows the slope factor and the
  expected highest point; the climate mod name shows both factors when they differ (e.g. *Scale 0.5 Heights 1*).
- Lake report: lowering depths in game metres use the height factor.
- Towns: option `ignore_town_size` (GUI: "Ignore real town size, start all at minimum population") - places are still
  chosen by population, then all towns are equal: they start at `min_start_capacity`, each gets
  `equal_town_slots` factories (industry plan, temperate: 2) and one long-distance cargo; roads and town ground no
  longer depend on the population. Industry presets need the new key `plan.equal_town_slots`.

## 0.1.0 - prototype (2026-10-18)

First public version: a technology study that generates Transport Fever 3 maps of real regions.

- Heightmap from an elevation model (OpenTopography download or GeoTIFF), with real water in the lowest valleys,
  painted rivers above, shaped banks and terrain evened out around towns and industries.
- Biome map and import layers (forest, meadow, cropland and crop types, rock, river bed, water, shore, glacier) from
  ESA WorldCover and OpenStreetMap.
- Towns at OpenStreetMap places and industries on suitable resource areas, as the map editor's import file.
- Climate mod *Alpine Realistic* with tree, rock and snow lines at real elevations, and the landscape *Alpine
  North-South* (gradual change to the dry southern Alps).
- Map mods: field tracks and hiking paths as streets, industry names after the nearest town.
- Local browser GUI and command line; Windows program without Python (portable ZIP). The GUI takes the lowest point
  of the elevation data as reference height, shows why *Generate* is not possible yet, and shows the values for the
  game's import dialog (minimum height raised by 3 m so the water is not too high).
- Climate mods are shared by maps with a similar lowest point: the mod's reference height is rounded to 100 m.
- `check` (and the GUI) report unknown game materials, warn when a project changes the look without an own climate
  name, and list the same problem of several nodes once.
- Example project *Innsbruck_Example* (map size Medium), walked through step by step in the docs.
- Graph framework: typed dataflow graphs (JSON), parameter sets (TOML), lineage cache; reference of all node types
  and graphs for AI assistants.

Not included: a road network between the places (experimental graph `map_roads`), looks for the dry and tropical
climates.
