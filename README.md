# tf3-AutoMapGen

*tf3-Auto-map-generator-framework* generates Transport Fever 3 maps of real regions from open geodata: an elevation
model, ESA WorldCover land cover and OpenStreetMap.

> **Status: prototype v0.1.** A working technology study: map makers can generate maps with the Windows program and
> the built-in *Alpine Realistic* look; developers can use it as a reference or as a base for their own tools. There
> is no support and no roadmap - see [Feedback](#feedback).
>
> Unofficial project, not affiliated with Urban Games or the publisher of Transport Fever.

<!-- Pictures from the game: e.g. Brenner valley overview, forest edges and fields close up, the north-south transition,
     a waterfall. Files in assets/pictures/, referenced here. -->

## Goal

**Skeleton maps that reproduce the environment of a real region in TF3 as realistically as possible:** the terrain,
the ground cover and the vegetation - where the forest ends, where meadows, fields, rock, scree and snow lie, where
the rivers run. Map creators use such a map as a base: they refine the details and add the gameplay elements
(stations, lines, further industries).

A second goal is a **modular architecture**: large changes to the map generation - other looks, regions, rules or
processing steps - are made by configuration, without changing code.

## Approach

When a heightmap is imported, TF3 does not simply display it. The game runs the **terrain generator of the map's
climate**: node graphs, comparable to shader graphs, that decide from height, steepness, biome and import layers which
ground texture lies where and where trees and rocks are placed. The base game climates know nothing about the real
landscape, and their height rules are made for the game's own, much lower random mountains. A real heightmap alone
therefore gives real shapes with generic vegetation - and snow at about 1,000 m a.s.l. on an Alpine map.

tf3-AutoMapGen produces three parts that fit together:

1. **Heightmap** - from an elevation model, with the water level problem solved (the game has a single water level:
   real water only in the lowest valleys, painted rivers above), shaped banks, and terrain evened out around towns and
   industries.
2. **Import layers** - black-and-white masks of forest, meadow, cropland, rock, river bed, water, shore and crop
   types from WorldCover and OpenStreetMap, plus the biome map. They tell the terrain generator *where* each kind of
   ground is.
3. **A climate mod** - the core of the project. It extends the game's own terrain generator so that it reads these
   layers and paints ground, forest and rocks at real elevations: tree line, rock line and snow line in metres above
   sea level. The look is defined as data in a **climate preset**; a map can also change its look gradually, e.g. from
   the green northern to the dry southern Alps on the Brenner map.

Tools that download and convert heightmaps for Transport Fever already exist. The climate mod and its layers turn such
a heightmap into a realistic landscape. How the game's terrain generator works and what the climate mod changes:
**[docsHuman/climate_mod.md](docsHuman/climate_mod.md)**.

In addition, tf3-AutoMapGen places towns at OpenStreetMap places (start size from the population) and industries on
suitable resource areas, and builds two small mods for each map: field tracks and hiking paths as streets, and
industry names after the nearest town.

## What a run produces

| Output | Use in the game |
|---|---|
| `<map>.png` (16-bit heightmap) and the import values (minimum, maximum and water height) | map editor: heightmap import |
| `<map>_biomes.png` and `<map>_<layer>.png` (forest, meadow, cropland, rock ...) | map editor: biome import and its layer fields |
| `<map>_industries.lua` (towns and industries) | map editor: towns and industries import |
| climate mod, e.g. `DHE-Alpine-Realistic-300m-Scale-0_5-BETA` | climate of the new map |
| map mods `DHE_<map>_path_streets` and `DHE_<map>_industry_names` | activated for the map |
| previews (`_biome_preview.png`, `_rivers_preview.png`, `_towns_preview.png` ...) | checks before the import |

If the game's user data folder is set, the run copies these files into the game's folders.

## Not included, limits

- **No road network.** Country roads and paths are worked into the terrain, but no road network between the places is
  built in the release. A new road planning between the planned towns and industries (minimum radius, grade limit,
  bridges and tunnels, junctions, terrain shaped under the roads) exists only as the experimental graph `map_roads`
  (not offered in the GUI) ([ADR 0013](docsAI/adr/0013-road-network-as-an-experimental-graph.md),
  [ADR 0016](docsAI/adr/0016-road-network-from-targets-without-osm.md)).
- **One climate preset:** *Alpine Realistic* (temperate base climate), with a transition to a southern Alpine look.
  Looks for the dry and tropical climates do not exist yet.
- **Tested in game on the author's maps only** (Alps: Brenner, Innsbruck, Zurich-Schaffhausen). Statements about the
  game are marked as verified or unverified in [docsAI/notes/tf3_facts.md](docsAI/notes/tf3_facts.md).
- **Requirements:** a TF3 installation (the climate mod is built from the game's files, which are not part of this
  repository), Windows (the build; the source runs with Python 3.13 elsewhere too, untested), and plenty of RAM for
  large maps (an 839 km² map needed 32-45 GB in the game's import).

## Quick start (map makers)

Download `tf3-AutoMapGen-<version>-windows.zip` from the releases, unpack it into a folder of your own and start
`AutoMapGen.exe`; no Python needed. Step by step, from the first start to the map in the game:
**[docsHuman/getting_started.md](docsHuman/getting_started.md)**.

## Quick start (developers)

```
git clone <this repository>
cd tf3-AutoMapGen
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

Settings of your computer go into `local/settings.toml` (not in git; the GUI writes them too):

```toml
game_folder = "C:/Program Files (x86)/Steam/steamapps/common/Transport Fever 3"
user_data_folder = "C:/Program Files (x86)/Steam/userdata/<number>/3493540/local"   # optional: copies into the game

[secrets]
opentopography_api_key = "..."   # free key from opentopography.org, used to download the elevation model
```

**GUI** (local, in the browser): `.venv/Scripts/automapgen.exe gui`. Choose the area on the map, the map size, the
scale, the heights and the look, tick the processing steps (download the source data, heightmap, biome map and layers,
towns and industries, climate mod, map mods, previews; copy into the game), then start the run. Afterwards the GUI shows the values for the game's import
dialog.

**Command line:**

```
automapgen projects                                  list the projects (configs/projects/*.toml)
automapgen run --project <id> --cache cache          generate a map
automapgen check --project <id>                      check graph and parameters without running
automapgen show map                                  print a graph as Mermaid diagram
automapgen nodes                                     list all node types with ports and parameters
```

The first run downloads the OpenStreetMap data from Geofabrik; WorldCover tiles are read over the network. A
step-by-step walk through a real map: **[docsHuman/example.md](docsHuman/example.md)**.

## How it works

A map is computed by a **graph**: small, pure **nodes** (read the elevation, compare a field with a limit, shape the
water, place towns, build the climate's node trees, write the heightmap ...) are wired together in a JSON **graph
document**. The settings live in layered **parameter sets** (TOML: preset, landscape, project), so one graph serves
many maps. Behaviour changes by rewiring or by changing parameters; code is only needed for a new kind of node.

```
data (typed values) --> nodes (pure functions) --> data ...
          wiring = graph document (JSON)     values = parameter sets (TOML)     engine = checks, order, cache
```

Principles, graphs and how the classes work together: **[docsHuman/architecture.md](docsHuman/architecture.md)**.

## Documentation

For people:

| File | Content |
|---|---|
| [docsHuman/getting_started.md](docsHuman/getting_started.md) | for map makers: install, first map, into the game, changing things with an AI assistant |
| [docsHuman/climate_mod.md](docsHuman/climate_mod.md) | the game's terrain generator, the import layers, what the climate mod does |
| [docsHuman/example.md](docsHuman/example.md) | the generation of a real map, step by step |
| [docsHuman/architecture.md](docsHuman/architecture.md) | principles, what a graph is, how the classes work together |
| [docsHuman/node_guide.md](docsHuman/node_guide.md) | for developers: writing nodes, graph and parameter formats |
| [docsHuman/graphs/](docsHuman/graphs/) | the built-in graphs as diagrams |

For AI assistants that help map makers change presets, projects and graphs:
[docsAI/reference/](docsAI/reference/README.md) (node types and graphs generated from the code).

Working documents (written for the AI assistant and maintainers - terse, one fact in one place):
[docsAI/](docsAI/) - current state, development log, requirements, code map, glossary, architecture decisions
([adr/](docsAI/adr/)) and verified knowledge about the game ([notes/tf3_facts.md](docsAI/notes/tf3_facts.md)).

## Development

```
.venv/Scripts/python.exe -m pytest              unit tests
.venv/Scripts/python.exe -m pytest -m slow      comparison with the predecessor project (needs it next to this one)
.venv/Scripts/python.exe -m mypy                strict type check
.venv/Scripts/python.exe -m ruff check src tests
```

`python packaging/build.py` (with `pip install -e ".[build]"`) builds the Windows program
(`dist/tf3-AutoMapGen-<version>-windows.zip`, [ADR 0017](docsAI/adr/0017-windows-build-as-portable-folder.md));
`automapgen reference` regenerates [docsAI/reference/](docsAI/reference/) after a change of nodes or graphs.

The project replaces a private predecessor. Its results are the reference for the comparison tests, which are skipped
when the predecessor is not present.

## How this project was made

The author set the goals, requirements and architecture, made every functional decision and tested the results in the
game. Code and documentation were written under this direction with the AI assistant Claude (Anthropic). The
architecture decisions, the code map and the verified game facts are part of the repository on purpose: they are in
[docsAI/](docsAI/) and show how the project is built and verified.

## Feedback

Issues and discussions are open and will be read, but there is no promise of an answer or a change. Forks, and the
reuse of ideas or code in other tools, are welcome - that is the purpose of this publication. Please keep the license
and attribution notices.

Related project: [TPF3 Map Studio](https://github.com/sharpi13822/TPF3-Map-Studio), a user-friendly desktop tool that
prepares real-world data for TF3 maps.

## Data sources and licenses

Generated maps contain third-party data. Name the sources when you publish a map:

- Map data: © OpenStreetMap contributors, Open Database License (ODbL 1.0)
- Land cover: ESA WorldCover 10 m 2021 v200, © ESA WorldCover project, CC BY 4.0
- Elevation: the model chosen via OpenTopography (e.g. GEDTM30, Copernicus GLO-30), each under its own license

Heightmap, layers and import files carry no attribution themselves; add it to the description of your map. The path
street mod names OpenStreetMap in its own description.

## License

Apache License 2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). Transport Fever 3 and its files are the property of
their owners and are not part of this repository.
