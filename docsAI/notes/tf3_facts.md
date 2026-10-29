# TF3 game facts

Verified in game, log or game files unless marked *unverified*. Taken over from the legacy project (2026-10-12).

## Map, heightmap, import

- 4 game metres per heightmap pixel (5121 px -> 20480 m). Map size presets in tiles of 256 m
  (`gui/menu/new_game_or_map_settings_page.tl`).
- Import: minimum height and water height are entered by hand; water height 0 = reference height.
- Import: with the computed minimum height ((black - reference) / scale) the water lies too high; +3 game m fits
  (e.g. -20 -> -17; verified by the user in game, several maps, 2026-10-18). The GUI shows the raised value
  (`app/gui/summary.py`, `MIN_HEIGHT_RAISE_GAME_M`). Cause *unverified*.
- Terrain materials of the base game: one zip per material in `base/content/terrain/materials/` (86 + `shared.zip`,
  read 2026-10-18); the zip name is the preset's `GameMaterial.name`; list in `docsAI/reference/README.md`.
- Biome PNG: 8-bit grey 0/63/127/191/255 = biome ID 0–4; biome 0 is computed by the game from height.
- At most 12 import layers (IDs 0–11, including the original `mountains`/`rivers`); higher IDs are silently ignored.
  The game samples layers coarser than 4 m (exact resolution unknown). Layer resolution 1 or 2 -> crash
  `generation_util::Blit`. A 1-pixel mask was invisible with ID 12, visible but far too wide with ID 11.
- Import layers are read as yes/no: a grey ramp 0..255 (Brenner transition mask) gave a hard edge exactly where the
  ramp leaves 0 (verified 2026-10-10). Grey values cannot carry a gradient; compute gradients in the tree.
- Ground textures (material tree) are regenerated on every LOAD of a map; trees/rock assets (import tree,
  `assets_out`) only on IMPORT.
- Biome import cannot be pre-filled: a patched copy of `gui/map_editor/map_editor.tl` inside a mod is not loaded.

## Climate mod

- The climate file path must contain `temperate.clima` (`…_temperate.clima.lua`), otherwise `base/mod.script.tl`
  hides all level crossings -> crash when building.
- Every climate needs a normal generator (not import, not editorOnly), else "No generator found for climate".
- Resource paths: base game `::/…`; own mod resources inside climate trees only `<modId>::/…` (`"/…"` resolves to the
  base game -> "Resource not found", black ground). In mod Lua use `require "::/..."` for base-game modules.
- Extra entries in `dataMaps.layers` of the `.clima.lua` appear as fields in the import dialog; read them in the import
  tree via `input_biomes` (`type = "Layer"`, `layerKey`), pass them to the material tree via persistent
  `output_map_data` -> `input_map_data`. Layers only take effect when the biome file is imported too.
- Node behaviour: `dithering_noise_map` returns 0..1. Original tree selection is `compare_map GREATER(density, noise)`
  (original densities 0.06–0.1). Clamp `fractal_noise_map` to 0.01..0.99 before comparisons (1 > 1 is false).
  A material output with weight < 1 and `dither = true` blends with the ground below (brown grass at 30 % on green
  grass looked like 30 %, verified 2026-10-10). `distance_map` (in1, threshold) - used by the base game for coasts
  (clamp 40..250); *unverified*: unit (assumed game metres) and which side of the threshold it measures.
  `assets_out.forest` / `assets_out.assets` expect integer indices – only multiply by 0/1 masks or use `combine_map`.
- Height rules of the temperate base climate in game metres (read from `climates.zip`, 2026-10-15; each rule fades in
  between the two values): material tree - scree 180/200 (`rock height profile      #0`), rock 330/350
  (`New Constant point cloud #8`), snow in hollows 330/390 (`rock height profile     #0`), snow 380/430
  (`Constant point cloud  #0`); import tree - tree density 0.06 up to 220, 0.03 at 250, none from 310
  (`heightrange and density`), mountain mask 100..1000 (`New Mask Map #376`). The random generator scales its
  mountain profile to about 380 m (`temperate_gen.tree.lua`, node `mountain height`: 0..0.6784 -> 0..380, not
  clamped). The rules read the terrain height (`height_map_input`); with water height 0 they count from the water
  level. TPF3 Map Studio measured in game: rock from about 325-350 m, snow from about 375-425 m above the water - the
  same values.
- Steepness: `gradient_map` with `direction = "X^2 + Y^2"` (only value found; others not in the exe text), present as
  `gradient_map0` in both trees. *Unverified:* that the value is tan² of the slope. Rock on steep slopes = node
  `map_clamp_map5` in the material tree (original 0.7–0.75).
- Tree sets: `temperate_plains` = brleaf_02 (index 6), `temperate_hills` = brleaf_01 (3), `temperate_conifer` =
  confr_01/02 (7). Rock asset sets = `categoryList` of the models in `base\content\assets\rocks` (granite, tundra_stone,
  coast_stone, sandstone …); temperate places none in the original (constant 0).
- Material `order`: higher lies on top. Game: grass 0–20, forest floor 400, gravel 500, scree 1000/10000, rock 2000,
  snow 12000, riverbed 80000.
- The same `.tmat` may be used by several outputs (the original uses `scree` twice). *Unverified:* a limit on the
  number of materials (24 run fine).
- `.tmat` fields: detailColorTexture / detailMetalGlossAoHTexture / detailNormalTexture; no opacity. H = alpha channel
  of the mgah texture (DXT5 in normal materials; `water_dirty` is DXT1, H = 255). *Unverified:* effect of H in game,
  and whether `::/` works in `.tmat` texture paths.
- `water_dirty` (paint brush in all.clima) is the only "water" ground texture; its colour texture is flat
  (41, 44, 41), the look comes from gloss + normal map. Real water is a shader, not a `.tmat`.
- Brush textures (all.clima, e.g. `gravel_02`, `sand_02`) live in `base\content\terrain\materials\*.zip`; terrain
  materials corn/wheat/soil have no `.gtex` (only dirt, gravel_03). The brush layer (`TerrainTileBrush`, per 256 m
  tile) cannot be set via the API.
- Classic Trees (`ug_tf2_trees`, mod.io 5879532) ships no models; it only sets `menuCategory` for TF2 trees already in
  the base game (`assets/vegetation/trees.zip`). *Unverified:* whether categories set at runtime reach the generator.

## Economy, towns, industries

- Climate -> `economy = "/economy/<x>.eco"`. The `.eco` removes industries (`placementParams.x = nil`), defines town
  cargos (`getCargoCategories`) and `townLevels2CargoInfos`. An industry is matched via `placementTag` of the `.con`
  and needs the economy in `categoryList`; cargos (`.cargo`) likewise ("Couldn't find cargo type for category
  'cement'" -> town demand disappears). Mod files have their own namespace: game `.con` files can only be copied, not
  replaced. Temperate lacks cement, paper, printing (subarctic), rubber, tyres (tropical).
- At most 5 stocks per industry (`industryutil.lua` `maxStocks = 5`, assert).
- Production rules of an industry are alternatives (`rules = { {...}, {...} }`): food factory = 2 fish + 1 sheet metal
  or 2 meat + 1 sheet metal (`requiredInput` per rule); raw-material industries: rule 1 without input, rule 2 with
  tools/machines/fertilizer. All other temperate factories have one rule. Verified: game files
  `base/content/industries/*.zip` (2026-10-28); read by `game.industry_rules`.
- Industry with 0 fields: `terminateConstructionHook` returns early -> full production (basis of compact industries).
- Towns/industries import = editor export format (`app.saveUserdata("towns_industries", …)`): towns with `name`,
  `position` (game metres, centre 0, north = +y), `sizeFactors`, one `com` and one `ind` demand; editor capacity =
  sizeFactor × 100. Industries: `fileName` `::/industries/<n>/<n>.con` (`tag` = name) or
  `<modId>::/industries/.../<file>.con` (`tag = ""`), `angle` (rad), `onWater`. Unknown `fileName` -> crash
  (`createIndustryProposal`), no skipping. No parameters, no names: the game picks a random town-district name.
- Town growth only from traffic experience (`town_growth.script.tl`), no upper limit, only with `developmentActive`.
- Editor crash: re-applying a towns/industries file while industries exist removes changed industries with their
  access roads -> `ecs::Engine::GetComponentDataIndex`. Apply only to a
  freshly imported map.
- Crash on exit only after "New game from map" with very many objects (INS3: 3.25 M), never after loading a save;
  workaround: save + reload.

## Scripts and constructions

- A `.gs.lua` in mod content is loaded as GameScript automatically. `sendCommand` with callback is forbidden in
  GameScripts ("Callbacks are currently disallowed"); check results via octree search. TF3 has no Lua console.
- A GameScript's local variables are not kept between calls of `update` (road test mod 2026-10-14: a lookup cached
  in a local ran on every call); keep everything in `state:get()/set()`. "Town street very small" is not in the build
  menu. Resource names: `::/infrastructure/bridge/concrete.bridge`, `::/infrastructure/tunnel/tunnel_a_car.tunnel`,
  mod resources `<modId>::/infrastructure/edge_addons/<name>.edge` (`api.res.<rep>.getAll()` lists them).
- Street templates are named `::/infrastructure/street/...street_template` / `<modId>::/…`; edges need `laneConfigs`.
  Junctions (≥ 3 edges) without BaseNodeConfig crash; with an empty config they build but without lane connections.
- Constructions: at most ~98 sub-constructions (200 -> crash `subIndex < 99`). Model `tag` in sub-constructions must be
  the module's string tag. A `.mdl` as ConstructionEntity -> assertion. Constructions' `updateFn(captureParams, params)`.
- More street/path/farm findings and crash causes: `paths_farms_roads_towns.md`.
- Edges (`BaseEdge`, `api/tealdef/api/engine.d.tl`) carry `type` (NORMAL/BRIDGE/TUNNEL) + `typeIndex` (bridge/tunnel
  type), `edgeDecorations : {{integer, boolean}}` (index in `api.res.edgeDecorationRep`, side) - the side walls and
  barriers of the build menu (`*.edge.lua` in `content/infrastructure/edge_addons/`) - and `roadDevelopmentLocked`
  (towns do not change the road). Street templates name decorations `"::/infrastructure/edge_addons/barrier_b.edge"`
  (`defaultEdgeDecorations`); the steepness of the cut and fill is the street type's `embankmentSlopeLow/High`
  (documented in the API; not yet tested from a script - road test mod, 2026-10-14).
- Log: `local\crash_dump\stdout.txt`.
- Base climate of a climate file is recognised by its file name: `base/base_mod.lua` uses
  `matchCategory(settings.climate, "dry.clima" / "tropical.clima")`, so a mod climate file must end in
  `<base>.clima` (verified 2026-10-12 in base.zip). Original import layers: temperate `rivers` 0, `mountains` 1;
  tropical `volcano` 0, `mountains` 1, `islands` 2; economies `temperate.eco`, `tropic.eco`.
