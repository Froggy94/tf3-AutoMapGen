# Getting started: your first map

This guide takes you from the download to a map in Transport Fever 3, step by step, with the built-in climate preset
*Alpine Realistic* and the standard graph `map`. You need no Python and no knowledge of graphs or nodes.

What you get for a region of your choice: a heightmap, a biome map with import layers (forest, meadow, fields, rock
...), a file with towns and industries, and a **climate mod** that paints forest, meadows, rock and snow at their real
elevations. Why the climate mod matters is explained in [climate_mod.md](climate_mod.md).

## What you need

- Windows 10 or 11, 64 bit, and Transport Fever 3.
- Memory: about 16 GB RAM for small and medium maps; very large maps need much more (the game's import of an
  839 km² map needed 32-45 GB). The page warns about large maps.
- Disk space: a few GB. The OpenStreetMap data of a country is downloaded once (Austria: about 700 MB).
- An internet connection while a map is generated.
- A free API key from [OpenTopography](https://portal.opentopography.org) (create an account, then "My Account" ->
  "Request API key"). It is used to download the elevation model.

## 1. Install

1. Download `tf3-AutoMapGen-<version>-windows.zip` from the [latest release](https://github.com/Froggy94/tf3-AutoMapGen/releases/latest). That file is the
   program; the "Source code" archives next to it are only for developers.
2. Unpack it into a folder of your own, e.g. `Documents\tf3-AutoMapGen`. **Not** below `C:\Program Files`: the
   program keeps your projects, downloads and maps in its own folder.
3. Start `AutoMapGen.exe` with a double click. The program is not signed, so Windows may show "Windows protected your
   PC": click *More info*, then *Run anyway*.

A console window opens and the page opens in your browser (`http://127.0.0.1:8765`). The console is the program:
keep it open while you work, close it to quit. If the page does not open, type the address into the browser.

## 2. One-time settings

On the left side of the page:

1. **Game folders**
   - *Transport Fever 3 installation*: the game folder, e.g.
     `C:/Program Files (x86)/Steam/steamapps/common/Transport Fever 3` (in Steam: right click on the game ->
     *Manage* -> *Browse local files*). The climate mod is built from the game's own files.
   - *User data folder of the game* (optional, recommended):
     `C:/Program Files (x86)/Steam/userdata/<number>/3493540/local`. If it is set, the results are copied straight
     into the game's folders for heightmaps, biomes, towns and mods.
   - Click *Save*.
2. **Elevation data**: paste the OpenTopography key into *API key* and click *Save*.

Both are stored in `local/settings.toml` on your computer only.

## 3. Try the example: Innsbruck

1. Under *Project*, choose **Innsbruck_Example** in the list and click *Load*. It is a map of size Medium (44 x 44
   tiles) around Innsbruck at scale 1:2, with the preset and all defaults.
2. Click **Generate** at the bottom.

The first run downloads the elevation model, OpenStreetMap data and land cover and then computes the map; this takes
a while (the console below the map shows the progress). Later runs reuse what is unchanged and are much faster.

At the end the page shows the values for the game's import dialog. The files are in `output/Innsbruck_Example/` and, with a
user data folder, already in the game.

## 4. Your own map

1. **Project**: click *New* and enter a **map name** (letters, digits, `_` and `-`).
2. **Climate preset**: *Alpine Realistic* for mountain regions of central Europe, *Lowland* for flat land
   with canals and rivers (only navigable waterways become real water, other rivers stay painted; if a canal of your
   region has no waterway class in OpenStreetMap, name it in the project, e.g.
   `navigable_names = ["Dortmund-Ems-Kanal"]` under `[params]`). A *landscape* such as *Alpine North-South* changes
   the look gradually across the map.
   **Reference height** (below it) = the lowest land in m above sea level; it becomes height 0 in the game. Leave it
   empty: then it is the lowest point of the elevation data (after a download the console names it). A value raises
   the land below it. The **climate mod** rounds it to 100 m, so maps with a similar lowest land share one mod.
3. **Graph**: keep **map - the complete map**. Graphs marked *experimental* are work in progress.
4. **Map**: choose the game's map size and the aspect ratio. **Scale** stretches the real region: at 1:2 one game
   metre stands for two real metres, so a larger region fits on the map and mountains become lower. **Heights 1:**
   sets a scale for heights alone: empty = like the distances, 1 = real heights, below 1 = higher than real. A
   smaller height scale than the distance scale makes slopes steeper: fewer towns on hills, more bridges and tunnels.
   The page shows the slope factor and, with a local elevation file, the highest point in the game.
5. **Location**: search a place, then drag the rectangle; a double click sets the centre. *Top of the map points to*
   turns the map.
6. **Elevation data**:
   - *Data set*: GEDTM30 is a good default (terrain without trees and buildings).
   - **Real water up to**: lakes and rivers up to this elevation become real game water; higher ones are painted. The
     game has only one water level, so keep this close to the reference height. Empty = 120 m above the reference
     height.
7. **Towns**: maximum number and minimum distance; empty = a default for the map size. Tick *Ignore real town
   size* to make all towns equal: the largest places still become the towns, but each starts as a hamlet, gets the
   same number of factories and one long-distance cargo, and grows in the game.
   *Advanced settings ...* has two more industry options. *Industry density* multiplies the number of industries
   and resource areas (2 = twice as many). *All cargos* places every raw material somewhere, even where the land
   does not fit it. On a flat map like the Münsterland there is no rock and no mountain, so quarries and ore mines
   only appear with *All cargos*. Without it, a steel mill whose ore has no source is left out, and so are the
   factories that need its steel.
8. **Processing steps**: keep all ticked for a new map. Later you can tick only what you want to redo, e.g. only
   *Climate mod*.
9. Click **Generate**.

*Show graph* draws what the program does, step by step; *Advanced settings ...* lists every setting with its
description. You need neither for a first map.

## 5. Into the game

1. In the game, open *Mods* and activate the climate mod (its name contains the preset, the reference height and the
   scale, e.g. *DHE Alpine Realistic 500m Scale 0.5 BETA*) and the map's mods (`DHE_<map>_path_streets`,
   `DHE_<map>_industry_names`).
2. Start the **map editor** with this climate and the map size and aspect ratio you chose.
3. Import the **heightmap** with the minimum and maximum height the page showed; water height 0.
4. Import the **biome map** `<map>_biomes.png` and set each layer field to its file (`<map>_forest.png` ...); the
   fields carry the names of the files.
5. Import the **towns and industries** file once, on the freshly imported map.
6. Save the map and start a game from it.

The walk-through of this example in [example.md](example.md) explains each step of the generation in detail.

## 6. Change looks and maps with an AI assistant

Everything the program does is described by text files: the look in `configs/presets/*.toml`, a gradual change of
look in `configs/landscapes/*.toml`, your map in `configs/projects/<map>.toml`, the processing in graph files
(`*.graph.json`). An AI assistant (e.g. Claude, ChatGPT) can change them for you if you give it the reference
[docsAI/reference/README.md](../docsAI/reference/README.md) and the file to change. Examples:

- "Here is my preset and the AI reference. Move the tree line to 1,900 m and make the alpine meadows drier."
- "Make a landscape for my project that changes from the Alpine look in the north to the southern look in the
  south, like `brenner_north_south.toml`."
- "My map has too many small towns. Which settings of `towns.plan` change that?"

Keep a copy of a file before you let an assistant change it, and check the result with a run. The page reports
settings that do not fit the graph before it starts.

## Problems

| What you see | What to do |
|---|---|
| "The program folder ... cannot be written" | Unpack the ZIP into a folder of your own (step 1). |
| *Generate* stays grey | The page lists the reasons below the button (map name, API key or GeoTIFF file, processing steps, settings that do not fit the graph). |
| "this run has no game folder" | Set the game installation under *Game folders*. |
| The download of the elevation model fails | Check the API key; without a university account it allows 50 requests a day. |
| The game crashes while importing | The map is too large for the memory; choose a smaller size. |

Issues can be reported on the project page; they are read, but there is no promise of an answer.

## When you publish a map

State in the description of the map that it was made with this program: **"Generated with DHE-tf3-AutoMapGen"**
(https://github.com/Froggy94/tf3-AutoMapGen). The map also contains third-party data; name the sources too: "Map data
© OpenStreetMap contributors (ODbL), land cover ESA WorldCover 2021 (CC BY 4.0), elevation <data set> via
OpenTopography". Details: [README.md](../README.md#publishing-a-map-credits).
