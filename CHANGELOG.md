# Changelog

## Unreleased

- Experimental road network (`map_roads`): bridges and tunnels only where the route search plans them, as straight
  spans at a constant grade (at most 6 %) with fitted approaches; bridges from 10 m above the ground (below: fills),
  tunnels from 12 m below and 150 m long (shorter: cuts); bridge costs grow with the height; steady grades instead of
  going up and down (ADR 0018).
- Faster terrain stamping of roads and paths (same result).

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
