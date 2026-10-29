# Glossary

One line per term: meaning; where it lives in code or config.

## Heights and map

- **game metre (game m):** length/height in the game. Parameters with `_game_m` are game metres.
- **m a.s.l.:** real elevation above sea level. Parameters with `_m` (without `game`) are real metres.
- **scale:** project parameter `scale` = 1:x (real metres per game metre); game m = real m / scale (distances; heights
  too unless a **height scale** is set). Climate mod name uses 1/scale ("Scale 0.5").
- **height scale:** project parameter `height_scale` (empty = `scale`): heights 1:height_scale, distances keep
  1:scale; 1 = real heights, below 1 = higher than real; slopes scale / height_scale times steeper.
  `MapFormat.height_game_per_real` (heights) vs. `game_per_real` (distances), ADR 0019; climate mod name adds the
  height factor when it differs ("Scale 0.5 Heights 1").
- **heightmap grid / frame:** 4 game m per pixel, (size / 4) + 1 pixels per side; `MapFrame` (`nodes/maps.py`).
- **reference height / lowest land:** real elevation that becomes game height 0 = the water level;
  `min_height_m` (empty = lowest point), output `references.land_min_m`.
- **black_m:** elevation of heightmap black = lowest land - `water_sink_m` (bottom of real water).
- **game height:** (real elevation - reference height) / height scale (= scale unless set); `GameHeights` (`nodes/climate/heights.py`).
- **map format:** game size (e.g. Gigantic) x aspect ratio (1:1 .. 1:5); `configs/map_sizes.toml`, `map.format`.

## Water

- **real water:** the game's single water level; only where the valley floor is below `real_water_until_m`.
- **painted water:** water above that level, as the `water` layer + material `water_dirty`; terrain unchanged.
- **river plan:** `_rivers.json`, `rivers.plan` -> `rivers.to_valley` (painted runs moved into the valley line, ADR 0011).
- **weir:** planned transition painted -> real water.
- **riverbed / groove:** painted river bed layer (`riverbed.paint`) / shallow terrain groove along its centre
  (`riverbed.groove`).
- **navigable waterway:** OSM way with a `CEMT` class, any way of the same name, a name starting with an entry of
  `navigable_names`, or an unnamed canal attached to one (`rivers._Navigability`); with `real_water_rivers =
  "navigable"` only these can be real water. Plan key per point: `navigable`.
- **ramp:** `riverbed.ramp`, the painted riverbed falling linearly to the water line over `ramp_length_game_m` where it
  meets real water, terrain within `ramp_corridor_game_m` following.

## Terrain chain

- **group:** a subgraph of `map` that follows the map-making logic (ADR 0015): `source_data`, `terraforming`
  (`elevation`, `biomes`, `rivers_and_water`, `draft_terrain`), `planning` (`paths_and_roads`, `towns`,
  `industries`), `final_terrain`, `biome_creation`, `climate`, `map_mods`; graph ids `map_<group>`; node paths go
  through them (`final_terrain/final/water`).
- **draft / final terrain:** the subgraph `terrain_core` used twice in `map`: draft for the planners
  (`terraforming/draft_terrain/draft`), final on the settled heights (`final_terrain/final`); only the final one is
  written.
- **settling:** `settle.terrain`, terrain evened around towns (tilted plane) and industries (level).
- **effective height:** height + steep bonus + noise; basis of the biome bands (`graphs/biomes.graph.json`).

## Game import and climate

- **import layer:** black/white PNG `<map>_<key>.png` read by the map editor; max 12 slots (0-11); yes/no only.
- **slot:** index of an import layer in the climate definition; `[defaults.climate.slots]` of the preset.
- **biome:** 0-4, grey 0/63/127/191/255 in `<map>_biomes.png`; biome 0 computed by the game.
- **import tree / material tree:** the climate's node graphs: trees and rocks once on import / ground textures on every
  load (`docsHuman/climate_mod.md`).
- **climate mod:** temperate copy with extended trees, one per reference height + scale; subgraph `climate_mod`.
- **climate preset / surface / look / patch / game area:** data description of the look (`ClimatePreset`,
  `nodes/climate/preset.py`); surface = area + look; patch = noise patches of another material; game area = an area the
  base climate computes ("lowland", "alpine_grass", "alpine_ground", "rock"), resolved by `TemperateAdapter`.
- **landscape / transition:** preset + a second look over part of the map (`configs/landscapes/`, `Transition`).
- **map mods:** the map's own mods (`nodes/map_mods/`): path streets, industry names; roads only in `map_roads`.
- **user data folder / staging_area:** the game's user folder (`local/settings.toml`); run destinations `heightmaps`,
  `biomes`, `towns_industries`, `staging_area` (ADR 0008).

## Framework

- **node / data type / graph / subgraph:** `@node`, `@data_type`, `<id>.graph.json`, node type `graph:<id>`.
- **parameter set / preset / landscape / project:** TOML layers; `[defaults]` = named values a graph uses where it
  exposes a parameter of that name; project = `configs/projects/<id>.toml`.
- **source / sink:** node reading external data (cached by parameters) / node with `side_effects=True` (writes, never
  cached).
- **lineage key:** cache key = hash(node type, version, code, parameters, input keys); ADR 0007.
- **run settings:** output target, downloads, game folder, user data destinations, secrets - never parameters.

## Project history

- **legacy project:** the private predecessor; functional template, unchanged.
- **golden master:** its stored reference run (`tests/golden/run`); comparison tests `-m slow`, allowed differences in
  `notes/legacy_differences.md`.
- **DHE:** the author's prefix in mod names and IDs.
