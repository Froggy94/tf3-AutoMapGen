# Code map

Package -> responsibility. Concepts: [../docsHuman/architecture.md](../docsHuman/architecture.md);
formats and node API: [../docsHuman/node_guide.md](../docsHuman/node_guide.md).

## Packages

```
src/automapgen/
  graph/            the framework - no domain knowledge (checked by tests/unit/test_architecture.py)
    __init__.py       public node API: node, data_type, NodeParams, param, NodeContext, DataType, Codec, Preview
    data.py           DataType, DataTypes catalog, built-in scalars (number, integer, text, flag)
    params.py         NodeParams (frozen, extra=forbid, attribute docstrings = descriptions), param()
    node.py           @node, NodeSpec (ports read from the signature), NodeContext, Arity
    catalog.py        NodeCatalog from modules and plugins (entry-point group "automapgen.nodes")
    document.py       GraphDocument (JSON model), GraphFile (load/save/schema)
    library.py        GraphLibrary: FolderLibrary (project before built-in), MemoryLibrary
    composer.py       GraphComposer: resolves `extends`
    paramsets.py      ParameterSet: TOML files, layers
    compiler.py       GraphCompiler: document + parameter set -> ExecutionPlan (flat, typed, sorted)
    plan.py           ExecutionPlan, PlannedNode, PortRef, ParameterSlot (which value of a parameter set reaches
                      which node parameters - the entries of the GUI's advanced settings)
    cache.py          LineageKeys, NodeCache: NoCache, MemoryCache, DiskCache
    engine.py         Engine, RunResult, CancelToken, RunListener, PreviewWriter
    render.py         MermaidDiagram
  nodes/            built-in node packages (domain); use only the public API of `graph`
    maps.py           MapFormat, MapFrame (heightmap grid over the real crop), Rotation; map.format
    rasters.py        Field, Mask, ClassMap (read-only values + frame, npz codec), resampling helpers
    fields.py         field.* (resample, slope, noise, ramp, sum, smooth, at_least)
    masks.py          mask.* (above, below, smooth, to_map, all, any, invert)
    classes.py        classes.* (bands, majority, select, to_map), landcover.worldcover
    elevation.py      ElevationSource; elevation.read|to_map|references, heightmap.encode
    images.py         Image (grey 8/16 bit or RGB, png codec); image.from_mask|palette|tint
    osm.py            GeofabrikIndex (region choice, download), OsmWays; osm.waterways
    rivers.py         RiverPlan (data of `_rivers.json`), RiverNetwork; rivers.plan (plan, preview, profiles)
    water.py          BedSegments; water.shape (lakes, rivers, banks, real water), water.layers (water/shore)
    river_valley.py   rivers.to_valley (painted rivers moved into the valley line; plan, preview)
    riverbed.py       riverbed.paint (bed + centre line), riverbed.groove
    farmland.py       CropPlan, FarmGrid; farmland.osm_fields|osm_preview (OSM), close_gap|parcels|field_paths
                      (WorldCover cropland cut into parcels)
    documents.py      JsonDocument: base of all plain-data plans (river plan, farm grid, path plan, road lines)
    lines.py          PathPlan, RoadLines; paths.plan, roads.from_osm (OSM lines cut and simplified),
                      roads.terrain, paths.terrain (terrain under them), lines.strip (forest gaps), previews
    towns.py          TownList, TextDocument; towns.plan (OSM places on buildable land, editor import file),
                      settle.terrain (terrain evened out around towns and industries)
    industries.py     IndustrySettings (a preset value), WorkGrid, SiteMaps, IndustryRules, IndustryList;
                      game.industry_rules (the game's industry files), industries.resource_areas,
                      industries.plan (placement, demand, names, import files)
    roads/            road network between towns and industries (ADR 0016, 0018). plan.py: RoadPlan (nodes with
                      game height, ways normal/bridge/tunnel); lattice.py: LatticeGraph (heading lattice: minimum
                      radius, grade and climb cost, straight structure moves: bridge/fill/cut/tunnel by height);
                      alignment.py: AlignmentBuilder (straights + arcs); profile.py: ProfilePlanner (where = the
                      structure legs; how = straight spans at a constant grade, fitted approaches, smooth normal
                      profile within fill and cut); network.py: roads.network (links,
                      spurs, sections, junctions, plan, terrain shaped under the normal ways, preview)
    opentopography.py DemDownload, OpenTopography: the crop of an elevation model downloaded once into the download
                      folder (used by elevation.read and elevation.read_on_map without a path)
    gamefiles.py      GameFiles (the game's base/content: folders and zipped folders, installation or local copy),
                      GameUserData (the player's user data folder: destinations of the copies), LuaData,
                      NodeTreeData; game.lua_file, game.node_tree
    climate/          the climate mod. preset.py: ClimatePreset (height rules, layers on slots, surfaces = area +
                      look, game ground rules, trees, rocks), Transition, Landscape - values of the preset files;
                      adapters.py: ClimateAdapter, TemperateAdapter (the only place knowing original node names);
                      factory.py, areas.py, patches.py, transition.py (ZoneRamp, TransitionMixer), material_tree.py,
                      import_tree.py: builders that add nodes to the game's trees; trees.py: climate.originals,
                      climate.material_tree, climate.import_tree; definition.py: ClimaDefinitionEditor,
                      GeneratorEditor; package.py: ClimateTexts, ClimateNaming, climate.identity|definition|
                      generators|pictures|files|mod_texts; waterfall.py: WaterfallSettings, WaterfallBrushes,
                      climate.waterfall; economy.py: EconomyPatcher (the one text patch: economies are Lua code),
                      climate.economy; classic_trees.py: climate.classic_trees; zones.py: ZoneMask,
                      climate.transition_mask (import layer of the transition area)
    mods.py           ModFiles (a mod or part of one), ModInfo, Translations, PublishingSettings, ImageStamper;
                      mod.package (content + mod.json, content index, mod hub texts, translations, thumbnail),
                      sink file.mod (<output>/mods/<folder>)
    map_mods/         the map's own mods: game scripts from Lua templates (template.py: LuaTemplate, templates/ as
                      package resources); info.py: mod.map_info (folder <prefix>_<map>_<kind>, mod ID, mod hub
                      texts); roads.py: mod.roads (builds the planned road network); path_network.py (junctions,
                      gaps, curves) and path_streets.py: mod.path_streets (paths as streets of own street types);
                      industry_names.py: mod.industry_names (industries named after the nearest town);
                      road_test.py: mod.road_test (test roads next to a marker street; graph road_test,
                      configs/road_test.toml)
    files.py          sinks file.png, file.height_range, file.json, file.text
  lua/              the game's Lua data files - a format library, knows neither `graph` nor `nodes`:
                      values (ordered tables, numbers kept as written), parser, writer (node editor layout),
                      DataDocument, NodeTree (generator trees: nodes, ports, parameters), TreeSignature (structural
                      comparison: names, positions and colours do not count)
  graphs/           built-in graph documents (ADR 0015). Maps: map = 7 groups, each its own graph `map_<group>`:
                    source_data, terraforming (= elevation, biomes, rivers_and_water, draft_terrain), planning (=
                    paths_and_roads, towns, industries), final_terrain, biome_creation, climate (= climate_mod +
                    sinks), map_mods; map_roads (experimental, extends map: final_terrain ->
                    map_final_terrain_roads with roads.network, map_mods -> map_mods_roads; ADR 0013, 0016),
                    map_parcels (experimental, extends map: terraforming/final_terrain -> *_parcels variants);
                    road_test (experimental test mod,
                    not bound to a map). Node paths go through the groups, e.g.
                    terraforming/draft_terrain/draft/water. Building blocks: terrain_core (water, layers, OSM fields, riverbed; no writing - used twice by a map),
                    terrain_core_parcels (extends terrain_core: fields from the landcover cut into parcels),
                    climate_mod (the climate mod for one reference height and scale), biomes, import_layer,
                    class_mask, mask_png
  app/              the application: projects.py (ProjectStore: configs/projects/<id>.toml, presets and landscapes
                    to extend), map_sizes.py (the game's sizes and formats, configs/map_sizes.toml),
                    local_settings.py (local/settings.toml: game, user data folder, secrets), toml_writer.py;
    gui/            the local browser GUI (`automapgen gui`): server.py (standard library http.server, 127.0.0.1
                    only), api.py (JSON routes), run.py (one run at a time in a thread with the engine, cancel,
                    cancel + delete), summary.py (values for the game's import dialog), graph_view.py (GraphView: graph
                    list, Mermaid diagram with subgraph ids, project check), tif_info.py, settings.py
                    (configs/gui.toml), page.html (one static page: Leaflet map, forms, console); advanced
                    settings (ADR 0010): advanced.py (entries from the plan's parameter slots, sections from
                    configs/gui.toml [advanced]), forms.py (form fields from JSON schemas, field kinds as
                    strategies), static/advanced.js + .css (the window; renderers by field kind)
                    reference.py (NodeReference, GraphReference, SchemaText: docsAI/reference/*.md from the
                    parameter schemas and graph documents; `automapgen reference`),
                    portable.py (PortableStart: start of the Windows build - program folder = working folder,
                    double click = gui, check that the folder is writable)
  cli.py            command line (nodes, check, show, run, schema, projects, gui; --project, graph and name from
                    the parameter sets)
packaging/          Windows build (PyInstaller, not part of the package): launcher.py (entry of AutoMapGen.exe),
                    automapgen.spec, build.py (WindowsBuild: exe folder + configs, assets, schemas, docsHuman, licences
                    of the dependencies -> dist/tf3-AutoMapGen-<version>-windows.zip)
```

The application depends on `graph` and `nodes`, never the reverse. The GUI edits project files (only the values of
the page; everything else of a project stays) and runs them with the cache. Processing steps (tick boxes) choose which
sinks a run writes: `app/steps.py` (`ProcessingStep`, `StepPlan`), steps as data in `configs/gui.toml [[steps]]`,
the engine's `targets` (ADR 0014).
