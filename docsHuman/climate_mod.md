# The climate mod: a realistic landscape in the game

A heightmap gives Transport Fever 3 the shape of a region. What the landscape *looks like* - which ground texture lies
where, where forest grows, where rock, scree and snow begin - is not part of the heightmap. The game computes it with
the terrain generator of the map's climate. This document explains that generator, the import layers that feed it, and
what the climate mod of tf3-AutoMapGen changes.

Statements about the game were checked in the game, its log or its files unless marked *unverified*. Details and
sources: [tf3_facts.md](../docsAI/notes/tf3_facts.md). Heights are given in **game metres** (m above the map's height 0)
or in **m a.s.l.** (real elevation above sea level).

## 1. The problem

The base game climates (temperate, dry, tropical) paint every map by generic rules. On a real region three things go
wrong.

**The real land cover is lost.** The game distributes forest randomly by biome and height. The real forests,
meadows, fields, rock faces and river beds of the region do not appear.

**The height rules do not fit real terrain.** The temperate climate is made for its own random maps, whose generator
scales mountains to about 380 game metres. Its rules (read from `temperate_clima.tree.lua` and
`temperate_import_gen.tree.lua`; each rule fades in between the two values):

| Rule | Game metres |
|---|---|
| scree | from 180-200 |
| tree line (trees thin out from 220, none above) | 310 |
| rock | from 330-350 |
| snow in hollows | from 330-390 |
| snow | from 380-430 |

A real Alpine region is much higher. On the Innsbruck example map (scale 1:2, game height 0 = 549 m a.s.l.) one game
metre is two real metres, so the base game would end the forest at 1,169 m a.s.l., paint rock from 1,209 m and snow
from 1,309 m a.s.l. In reality the tree line lies at about 2,000 m and permanent snow above 2,500 m.

**The game has a single water level.** A river that falls several hundred metres across the map cannot be real water
everywhere. Above the water level it has to be painted, and the base climates have no ground for painted water.

## 2. The game's terrain generator

A climate consists of Lua files in `base/content/climates/<climate>/` of the game (or in a mod):

| File | Role |
|---|---|
| `<climate>.clima.lua` | climate definition: names, biomes, import layers, tree sets, economy, generators |
| `<climate>_import.gen.lua` with the **import tree** `<climate>_import_gen.tree.lua` | runs **once, when a map is imported** in the map editor: reads heightmap, biome map and import layers, places trees and rock assets, stores data for the material tree |
| `<climate>.gen.lua` with `<climate>_gen.tree.lua` | random map generator; the game needs one per climate |
| **material tree** `<climate>_clima.tree.lua` | runs **every time a map is loaded**: decides for every point of the terrain which ground textures (materials) are painted, with which weight and in which order |

The trees are **node graphs**, comparable to shader graphs or the node editors of Blender and Houdini. Nodes such as
`fractal_noise_map`, `compare_map`, `gradient_map` (steepness), `distance_map` and `map_clamp_map` compute maps from
the inputs - height, steepness, biome, import layers, noise - and end in outputs: material outputs with a paint order
(higher lies on top) and asset outputs (tree or rock index per cell).

Three properties shaped the design of the mod:

- **Trees and rocks are placed only on import, ground textures on every load.** A changed material tree changes
  existing maps; changed forests need a new import.
- **The material tree cannot read the import layers directly.** The import tree stores them as persistent map data,
  and the material tree reads that data.
- **Steepness** comes from `gradient_map`. Its unit is assumed to be tan² of the slope (*unverified*). The game uses it
  to put rock on steep slopes.

## 3. Import layers and masks

The import dialog of the map editor takes a **heightmap**, a **biome map** and one **layer image** for each import
layer the climate declares. The game stretches every image to the map size.

- **Heightmap:** 16-bit grey PNG, 4 game metres per pixel. Minimum height, maximum height and water height are entered
  by hand; tf3-AutoMapGen prints the values after every run.
- **Biome map:** 8-bit grey, grey values 0 / 63 / 127 / 191 / 255 for biomes 0-4. Biome 0 is computed by the game
  from the height; tf3-AutoMapGen writes biomes 1-4.
- **Import layers:** black-and-white PNGs. The temperate climate declares two (`rivers`, `mountains`); the climate mod
  keeps `mountains` and leaves `rivers` out (unused). Every further entry in `dataMaps.layers` of the climate
  definition adds a field to the import dialog.

How the game treats layers (all checked in the game):

- **At most 12 layers** (slots 0-11, the original ones included). Layers on higher slots are ignored without a message.
- **Yes or no only.** A grey ramp produces a hard edge where it leaves 0; gradients have to be computed in the tree.
- **Coarse sampling.** The game samples layers more coarsely than its 4 m grid. Each layer has a resolution setting;
  8 and 16 work, 1 and 2 crash the import. Narrow lines such as paths cannot be layers.
- **Layers need the biome map.** They only take effect when the biome map is imported too.

The layers of the preset *Alpine Realistic* (the key is also the file name `<map>_<key>.png`):

| Slot | Layer | Source |
|---|---|---|
| 0 | `transition_mask` on maps with a transition (the base climate's `rivers` layer is left out) | the area where the second look has begun |
| 1 | `mountains` (original) | mountain mask from the heights |
| 2 | `forest` | WorldCover tree cover, minus water, river beds, fields and gaps along paths and roads; optionally shrunk along its edge (`forest_shrink_px`), because the game reads the layer coarsely and plants a whole cell as soon as it holds forest |
| 3 | `meadow` | WorldCover grassland, shrubland, built-up area, wetland, moss; cropland without an OSM field |
| 4 | `cropland` | OpenStreetMap fields |
| 5 | `rock` | WorldCover bare and sparsely vegetated ground |
| 6 | `riverbed` | painted river beds |
| 7 | `water` | painted water: lakes and rivers above the game's water level |
| 8 | `shore` | banks of lakes and rivers |
| 9-11 | `crop_corn`, `crop_wheat`, `crop_soil` | crop types of the fields, by field size and the OSM tag `crop` |

## 4. What the climate mod changes

The climate mod is a **copy of the temperate climate with extended trees**, generated for each map from the game's
own files. It does not replace the game's logic but adds to it; where no layer says otherwise, the familiar look stays.

**Import tree - trees and rocks:**
- reads every layer and stores it for the material tree;
- places **forest exactly where the forest layer is**: tree index per cell = forest mask x density; species by biome
  and height (more conifers from 800 m a.s.l., none above the tree line); no trees on steep, wet or high ground;
- places **boulders** by area - in mountains, forests, on banks, in river beds and below waterfalls - in noise
  patches, with rock sets (granite, tundra stone, coast stone) chosen by share;
- optionally mixes the TF2 trees of the base game into the forest (Classic Trees).

**Material tree - ground textures:**
- moves the **height rules to real elevations**: the preset *Alpine Realistic* sets scree from 2,100 m, rock from
  2,200 m and snow from 2,500 m a.s.l., the tree line at 2,100 m a.s.l. (section 5);
- adds **surfaces**: each kind of ground is an *area* - a layer, an area the game computes itself such as "lowland"
  or "alpine grass", or the product of both - with a *look*: a material output and optional noise patches of other
  materials on top. Examples: meadow = meadow layer x lowland, green grass with 30 % patches of light green grass;
  fields = dark green grass, with corn, wheat and soil textures on the crop layers; river bed = scree with rock
  patches; painted water = the game's texture `water_dirty`;
- extends the game's own rules: the rock layer adds rock on steep and scree on flat ground, the forest layer adds
  forest floor;
- paints **waterfalls** where painted water is itself steep, with seamless waterfall brushes (white foam on the colour
  of the game's water texture), generated by the mod.

**Around the trees:** the climate definition with the own layers on their slots, the generators, the climate's tile in
the "New map" menu showing reference height and scale, mod texts in German and English, and optionally an own economy.

### One mod per reference height and scale

The trees work in game metres. To keep the snow line at 2,500 m a.s.l. on every map, the generator converts every
elevation of the preset:

*game height = (real elevation - reference height) x scale*

A climate mod is therefore valid for one reference height and one scale, and its name states both, e.g.
`DHE Alpine Realistic 500m Scale 0.5 BETA`. Maps with the same reference height and scale share one mod.

So that maps with a similar lowest land share one mod, the mod's reference height is the map's lowest land rounded
to 100 m (`climate_reference_step_m`, 0 = exact; 550 m and 560 m both give 600 m). The heightmap keeps the exact
lowest land as game height 0. The mod's height rules therefore shift by the difference, at most half a step: on the
Innsbruck example map (lowest land 549 m, mod 500 m, scale 1:2) 2,500 m a.s.l. becomes (2,500 - 500) x 0.5 = 1,000
game metres, which on this map lies at 2,549 m a.s.l.

## 5. The look as data: presets and transitions

Everything the mod paints is described in a **climate preset**, a TOML parameter set
([configs/presets/alpine_realistic.toml](../configs/presets/alpine_realistic.toml)): height limits, layers and their
slots, surfaces with areas and looks, forest and rock settings. A surface:

```toml
[defaults.climate.surfaces.meadow.area]
kind = "product"
first = { kind = "layer", key = "meadow", hard = false }
second = { kind = "game", name = "lowland" }

[defaults.climate.surfaces.meadow.look.base]
kind = "output"
material = { kind = "game", name = "grass_green" }
order = 5
dither = true
```

A new look is a new preset that `extends` an existing one and states only the differences - no code.

**Lowland** ([configs/presets/lowland_realistic.toml](../configs/presets/lowland_realistic.toml)) is
such a preset, for flat land with canals and rivers. It extends *Alpine Realistic* and changes the water and the
look:

- **Waterway classes** (`real_water_rivers = "navigable"`): in flat land nearly every river lies below the real water
  limit, and real water is cut down to the game's single water level, so every brook would become a trench. With
  this setting only navigable waterways become real water: ways with an inland waterway class in OpenStreetMap
  (`CEMT` tag), every way of the same name, names listed in `navigable_names` (prefix match, e.g.
  `["Dortmund-Ems-Kanal"]` where OSM has no class) and unnamed canals attached to them. Other rivers stay painted
  water at their width, also where a canal crosses them; lakes a painted river flows through stay painted as well
  (e.g. a lake on a brook: no crater down to the water line). The default `"all"` keeps the earlier behaviour.
- **Ramps** (`ramp_length_game_m = 500`): where a painted riverbed meets real water, the bed falls linearly to the
  water line over this length and the terrain beside it (`ramp_corridor_game_m`, 60 game m on each side) follows;
  without it the painted river would end at a step above the canal. 0 = no ramp.
- Forest shrunk by 2 px along its edge, about a third conifers below the conifer height (Münsterland: 67 % broadleaf,
  33 % conifer, Wald und Holz NRW, Landeswaldinventur 2022), no boulders in forests, on shores and in riverbeds, and
  its own mod name and texts ([configs/mod_texts/lowland.toml](../configs/mod_texts/lowland.toml)).

A **landscape** can change the look across one map. The Brenner map changes from the green northern Alps to the
drier southern Alps: between the Inn valley (47.27° N) and Brixen (46.72° N) the share of the southern look rises
along the latitude. The tree computes this rise from the distance to the start line, because layers cannot carry
gradients. A factor per surface and biome limits the share, and a mixing mode decides how the two looks combine
(overlay, noise patches or a soft blend). Example:
[configs/landscapes/brenner_north_south.toml](../configs/landscapes/brenner_north_south.toml).

## 6. How the mod is built

- The generator reads the original climate files from the local game installation (setting `game_folder`). They are
  never part of this repository; the generated mod contains modified copies, as climate mods do.
- The trees are edited as **Lua objects** - with an own parser and writer that keep the game's format and the node
  editor layout - not as text. Own nodes carry the prefix `lc` and are laid out in columns, so the result stays
  readable in the game's node editor.
- Only one class knows the names of the original nodes (`TemperateAdapter`). If a game update renames a node, the
  build fails there with a clear message instead of producing a broken mod.
- The only text patch is the optional economy, because economy files are Lua code, not data. Every replacement must
  match exactly once.

Code: `src/automapgen/nodes/climate/` (overview in [code_map.md](../docsAI/code_map.md)); graph:
[graphs/climate_mod.md](graphs/climate_mod.md).

## 7. Open questions

- The unit of `gradient_map` (tan² assumed).
- The resolution at which the game samples import layers.
- The other base climates (dry, tropical) need their own adapter and presets.
