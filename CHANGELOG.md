# Changelog

## 0.1.0 - prototype (unreleased)

First public version: a technology study that generates Transport Fever 3 maps of real regions.

- Heightmap from an elevation model (OpenTopography download or GeoTIFF), with real water in the lowest valleys,
  painted rivers above, shaped banks and terrain evened out around towns and industries.
- Biome map and import layers (forest, meadow, cropland and crop types, rock, river bed, water, shore, glacier) from
  ESA WorldCover and OpenStreetMap.
- Towns at OpenStreetMap places and industries on suitable resource areas, as the map editor's import file.
- Climate mod *Alpine Realistic* with tree, rock and snow lines at real elevations, and the landscape *Alpine
  North-South* (gradual change to the dry southern Alps).
- Map mods: field tracks and hiking paths as streets, industry names after the nearest town.
- Local browser GUI and command line; Windows program without Python (portable ZIP).
- Graph framework: typed dataflow graphs (JSON), parameter sets (TOML), lineage cache; reference of all node types
  and graphs for AI assistants.

Not included: a road network between the places (experimental graph `map_roads`), looks for the dry and tropical
climates.
