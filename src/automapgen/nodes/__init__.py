"""Built-in node packages: map frame, rasters (fields, masks, classes), elevation, images, OSM, rivers, water,
riverbed, fields, lines, towns, industries, road network (roads), game files, mods, climate, map mods, files."""

from types import ModuleType

from automapgen.nodes import (
    classes,
    documents,
    elevation,
    farmland,
    fields,
    files,
    gamefiles,
    images,
    industries,
    lines,
    maps,
    masks,
    mods,
    osm,
    rasters,
    river_valley,
    riverbed,
    rivers,
    towns,
    water,
)
from automapgen.nodes.climate import classic_trees as climate_classic_trees
from automapgen.nodes.climate import economy as climate_economy
from automapgen.nodes.climate import package as climate_package
from automapgen.nodes.climate import trees as climate_trees
from automapgen.nodes.climate import waterfall as climate_waterfall
from automapgen.nodes.climate import zones as climate_zones
from automapgen.nodes.map_mods import industry_names as map_mods_industry_names
from automapgen.nodes.map_mods import info as map_mods_info
from automapgen.nodes.map_mods import path_streets as map_mods_path_streets
from automapgen.nodes.map_mods import road_test as map_mods_road_test
from automapgen.nodes.map_mods import roads as map_mods_roads
from automapgen.nodes.roads import network as roads_network
from automapgen.nodes.roads import plan as roads_plan

BUILTIN_MODULES: tuple[ModuleType, ...] = (
    maps, rasters, documents, fields, masks, classes, elevation, images, osm, rivers, river_valley, water, riverbed,
    farmland, lines, towns, gamefiles, industries, roads_plan, roads_network, mods, climate_trees, climate_package,
    climate_economy, climate_waterfall, climate_classic_trees, climate_zones, map_mods_info, map_mods_roads,
    map_mods_road_test, map_mods_path_streets, map_mods_industry_names, files)
