# Example: the Brenner map step by step

This walk-through follows one real map through the generator: **Brenner**, the Alpine crossing from the Inn valley
north of Innsbruck over the Brenner pass down to Bolzano. For each step it says what happens and why. The values are
those of the project file [configs/projects/brenner.toml](../configs/projects/brenner.toml); the graph is
[map](graphs/map.md). The game's terrain generator and the climate mod are explained in
[climate_mod.md](climate_mod.md).

<!-- Pictures: a preview of each step (files of the output folder) and the result in game. -->

The steps below follow the groups of the graph `map` (in the GUI: "Show graph"):

| Steps | Group (node path) |
|---|---|
| 1, 2 (reading), 4 | Source Data (`source_data`) |
| 2 (references), 3, 5, 6 | Terraforming (`terraforming`: `elevation`, `biomes`, `rivers_and_water`, `draft_terrain`) |
| 7 | Planning (`planning`: `paths_and_roads`, `towns`, `industries`) |
| 8, 9 | Final Terrain (`final_terrain`) |
| 10 | Biome Creation (`biome_creation`) |
| 11 | Climate Mod (`climate`) |
| 12 | Map Mods (`map_mods`) |

## 0. The project: three layers of settings

```
configs/presets/alpine_realistic.toml           look "Alpine Realistic": layers, surfaces, height limits, forest, rocks
   ^ extends
configs/landscapes/brenner_north_south.toml     + transition to the southern Alpine look (Inn valley -> Brixen)
   ^ extends
configs/projects/brenner.toml                   the map: region, size, scale, heights, towns, a few changes
```

The project file contains only what is specific to this map:

```toml
graph = "map"
extends = ["../landscapes/brenner_north_south.toml"]

[params]
map_name = "Brenner"
crop = { west = 11.28289, south = 46.45057, east = 11.71366, north = 47.33963 }
game_width_km = 16.384           # map size Gigantic, format 1:3
game_height_km = 49.152
scale = 2                        # 1:2 - one game metre stands for two real metres
min_height_m = 250               # lowest land = game height 0
water_sink_m = 40                # bottom of real water 40 m below it
real_water_until_m = 270         # real (game) water only up to this elevation
max_towns = 32
town_min_distance_game_m = 3000
biome_limits_m = [1000, 1600, 2100]
```

`automapgen run --project brenner --cache cache` (or *Run* in the GUI) compiles the graph with these layers, checks it
completely and runs it. With the cache, a later run computes only what a change affects.

## 1. Map format and frame

**What:** `map.format` turns game size and scale into the heightmap grid: 16.384 x 49.152 game km at 4 m per pixel
gives **4097 x 12289 pixels**, covering 32.8 x 98.3 km of real land. `elevation.to_map` places this frame over the
crop, optionally rotated. Every later step works on this grid.

**Why:** the game stretches every imported image to the map size, so all images must have the map's aspect ratio and
grid.

## 2. Elevation

**What:** `elevation.read` reads the elevation model, here GEDTM30 (30 m resolution, terrain without trees and
buildings). Without a file (`dem_path`) the model is downloaded once from OpenTopography. `elevation.to_map` fills
gaps and resamples the model bicubically to 4 m. `elevation.references` sets the **height references**: the lowest
land, 250 m a.s.l., becomes game height 0; the bottom of real water lies 40 m lower, at 210 m a.s.l. (black in the
heightmap).

**Why:** a terrain model without vegetation is needed - a surface model would show forests as hills, and the game
adds its own trees on top. The references fix where the game's single water level lies.

## 3. Biomes and the mountain mask

**What:** the subgraph `biomes` ([diagram](graphs/biomes.md)) computes an *effective height* (height, plus extra
height on steep slopes, plus smooth noise), cuts it at 1000 / 1600 / 2100 m into biomes 1-4 (valley, mountain forest,
subalpine, alpine) and smooths the borders with a majority filter. Land with an effective height above 1800 m
forms the mountain mask. Output:
the biome map `Brenner_biomes.png`, the layer `Brenner_mountains.png` and the preview `Brenner_biome_preview.png`.

**Why:** the biomes select tree species and the game's own ground types. The extra height on steep slopes and the
noise give natural, irregular borders instead of contour lines.

## 4. Land cover

**What:** `landcover.worldcover` reads ESA WorldCover (10 m) tile by tile and assigns each heightmap pixel the most
frequent class: tree cover, grassland, cropland, bare ground, snow and ice, water, built-up area and others. Preview:
`Brenner_landcover_preview.png`.

**Why:** WorldCover is the real land cover - the source of the forest, meadow, rock and water layers.

## 5. Rivers: real and painted water

**What:** `osm.waterways` reads the rivers and canals of OpenStreetMap (without tunnels and culverts), and
`rivers.plan` joins them into a continuous, directed network and determines the valley floor along each river. Where
the valley floor lies below **270 m a.s.l.** (`real_water_until_m`) over at least 1 game km, the river becomes **real
water** from there downstream, at the game's water level, with a width taken from WorldCover. Upstream, rivers are
**painted**: a river bed of 10-20 game metres in a layer, without changing the terrain. Where painted water meets real
water, a **weir** is planned. `rivers.to_valley` then moves the painted courses up to 100 m sideways into the valley
line of the elevation model where OpenStreetMap and the 30 m model disagree
([ADR 0011](../docsAI/adr/0011-painted-rivers-in-the-valley-line.md)). Previews: `Brenner_rivers_preview.png`,
`Brenner_rivers_profiles.png`, `Brenner_rivers_valley_preview.png`.

**Why:** TF3 has one water level per map. The Inn at Innsbruck (about 570 m a.s.l.) lies some 300 m above the Eisack
at Bolzano. Making it real water would flood the southern valleys or require a canyon through the Alps. On this map
only the lowest valley floors in the south become real water; all other rivers are painted, which looks right from the
usual camera distance.

## 6. The draft terrain

**What:** the subgraph `terrain_core` ([diagram](graphs/terrain_core.md)) runs for the first time, as the *draft*:
- `water.shape` levels the lakes, cuts the real river bands into the terrain and shapes their banks;
- `water.layers` computes the water and shore layers, `riverbed.paint` the painted river beds;
- `farmland.osm_fields` rasterises the OpenStreetMap fields and gives each group of fields a crop type, by size: large
  fields mostly wheat and corn, small ones mostly grass;
- finally the class layers (forest, meadow, cropland, rock, glacier) are cut free of water, banks, river beds and
  fields.

**Why:** the planners of the next step must see the map as the game will: where water, forest and fields are, and
which land can be built on.

## 7. Planning on the draft

**What:**
- `paths.plan` selects field tracks (paved ones; in forests and towns only main tracks) and hiking paths from
  OpenStreetMap; `roads.from_osm` selects the country roads. Both are lines that are worked into the terrain later.
  Previews: `Brenner_paths_preview.png`, `Brenner_roads_osm_preview.png`.
- `towns.plan` places up to **32 towns** at OpenStreetMap places with a population, largest first, at least 3 game km
  apart, on buildable land - dry, gently sloped, no rock or glacier - moving a town to a suitable spot nearby if
  necessary. The start size follows the real population. Output: entries of the import file and
  `Brenner_towns_preview.png`.
- `industries.resource_areas` finds building sites and resource areas in the terrain as the game will see it (forest
  for forestry, rock for quarries, flat land for farms and others). `industries.plan` places raw-material industries
  in the resource areas and processing industries near medium and large towns. It uses the production rules read from the
  game's own industry files (`game.industry_rules`) and names each industry after the nearest place. Output:
  `Brenner_industries.lua` (towns with their demand, and industries) and previews of sites, resources and industries.

**Why:** towns and industries belong where they are in reality and where the game can build them - not on a 30 %
slope or in a river.

## 8. Settling

**What:** `settle.terrain` evens out the terrain around every town (a tilted plane with limited slope) and every
industry (level), with soft, slightly irregular edges. Preview: `Brenner_settle_preview.png`.

**Why:** TF3 towns grow poorly on rough slopes, and industries need level ground. A 30 m elevation model is never
smooth enough for either.

## 9. Final terrain and heightmap

**What:** `terrain_core` runs a second time, as the *final* terrain on the settled heights, so that water, banks and
all layers match the terrain that is written. Then `roads.terrain` and `paths.terrain` work the country roads and
paths into the terrain (level across, a smoothed profile with limited grade, cut and fill with banks), and
`riverbed.groove` presses a shallow groove along the painted river beds. `heightmap.encode` writes the **16-bit
heightmap** `Brenner.png` (black = 210 m a.s.l., white = the highest point), and `file.height_range` stores the
references for the import values.

**Why the terrain is computed twice:** settling changes the heights, so layers computed before it would no longer fit.
The draft exists only for the planners; nothing of it is written.

## 10. Import layers

**What:** the final layers are written as black-and-white PNGs on the heightmap grid: `Brenner_forest.png` (minus
the gaps along paths and roads), `_meadow`, `_cropland`, `_rock`, `_water`, `_shore`, `_riverbed`, `_crop_corn`,
`_crop_wheat`, `_crop_soil`, and `Brenner_transition_mask.png` (the area where the southern look has begun).

**Why:** these layers are the input of the climate mod's trees - they say *where* each kind of ground and vegetation
is ([climate_mod.md, section 3](climate_mod.md#3-import-layers-and-masks)).

## 11. The climate mod

**What:** the subgraph `climate_mod` ([diagram](graphs/climate_mod.md)) reads the original files of the temperate
climate from the game installation and builds the mod **`DHE Alpine Realistic 300m Scale 0.5 BETA`**, valid for the
reference height 300 m a.s.l. (the lowest land, 250 m, rounded to 100 m) and the scale 0.5 game metres per real metre:
- **import tree:** forest from the forest layer, species by biome and height, boulders in mountains, river beds and
  below waterfalls;
- **material tree:** the surfaces of the preset - meadow, fields with crop textures, alpine grass, scree, rock, snow,
  painted water, shore, river bed, waterfalls - with height rules at real elevations. Snow, for example, starts at
  2,500 m a.s.l., which is game height (2,500 - 300) x 0.5 = 1,100 m; on this map (game height 0 = 250 m) that is
  2,450 m a.s.l., 50 m lower because of the rounding. The base game would start it at 380 game metres, i.e.
  1,010 m a.s.l.;
- **transition:** between the Inn valley (47.27° N) and Brixen (46.72° N) the southern look - browner grass, drier
  rock - rises to its full share, set per surface and biome;
- the climate definition with the own layers on their slots, the climate tile with "300m / Scale 0.5", and the mod
  texts.

**Why:** without the mod the game ignores the layers and paints generic vegetation at the wrong heights
([climate_mod.md, section 1](climate_mod.md#1-the-problem)).

## 12. The map's own mods

**What:** `mod.path_streets` builds a mod with a game script that builds the planned field tracks and hiking paths in
the game as streets of own, unpaved street types. `mod.industry_names` builds a mod that names every industry after
the nearest town, including industries founded later.

## 13. Into the game

If `user_data_folder` is set, the run copies the heightmap to `heightmaps`, the biome map and the layers to
`biomes`, the import file to `towns_industries` and the mods to the game's mod staging area. The GUI then shows the
values for the import dialog: minimum and maximum height in game metres, water height 0.

<!-- To be checked against the actual procedure in the game. -->
1. Activate the climate mod and the map mods, and open the map editor with the climate
   *DHE Alpine Realistic 300m Scale 0.5 BETA* and the map size *Gigantic*, format 1:3.
2. Import the heightmap with the values shown.
3. Import the biome map `Brenner_biomes.png` and set each layer field to its file (`Brenner_forest.png` ...); the
   fields carry the names of the files.
4. Apply the towns and industries file once, on the freshly imported map.
5. Save the map, then start a game from it.

## What happens when something changes

- **`biome_limits_m` in the project:** only the biomes, what depends on them and the climate files are recomputed;
  rivers and elevation come from the cache.
- **A new look** (e.g. different grass): change the preset or a landscape that extends it; only the climate mod is
  rebuilt.
- **A different processing step** (e.g. another town placement): a graph that `extends` the group and replaces one
  node, and a map graph that `extends` `map` and uses that group; all other steps remain those of the standard graph.
  The experimental `map_parcels` does exactly this for the draft and the final terrain
  ([architecture.md](architecture.md)).
