# Deliberate differences to the legacy project

The comparison tests (`tests/comparison/test_legacy_map.py`) demand pixel-identical results except for the cases listed here. Each one
is a legacy bug or a decision; the test allows differences only where they are expected.

| Since | Where | Legacy | Here | Why |
|---|---|---|---|---|
| 2026-10-12 | forest layer, riverbed clearing | when every painted riverbed piece lies under water, the riverbed mask is empty; `distance_transform_edt(~bed)` without background measures to a virtual point outside the top-left corner and the forest is cleared in a quarter circle of 4 px at that corner | `mask.near` returns an empty mask for an empty mask | legacy bug (visible on the golden-master region: 12 px) |
| 2026-10-12 | river preview, profiles | German labels | English labels | everything in this project is English; previews are not compared |
| 2026-10-12 | lake list in the log | names from OSM areas (`natural=water`, slow area assembly) | "lake <id>" | log only; names can come back as an own node when wanted |
| 2026-10-12 | forest gaps along paths | width per path kind (paths, trails) | one width per plan (`lines.strip`) | simpler; both 0 in the legacy settings |
| 2026-10-12 | path preview | planned roads of an earlier run drawn in | not drawn | previews only; the road network has a preview of its own |
| 2026-10-12 | settle preview | no file when nothing is settled | a 1 x 1 picture | sinks always run; previews only |
| 2026-10-12 | town size classes in the log | German names (Weiler, Dorf ...) | English (hamlet, village ...) | log only |
| 2026-10-12 | own resource areas | drawn into `_resources_preview_edit.png`, kept in `_resources_manual.npz` | not yet | needs a GUI editor; not used by the golden master |
| 2026-10-12 | `_resources.npz` | site, score and region arrays written | not written | no consumer in the pipeline; the areas are a node output (`resource_maps`) |
| 2026-10-12 | industry previews | German labels | English labels; the industry type names stay German (they are names in game) | previews only |
| 2026-10-12 | paths preview | drawn from the unrounded lines | drawn from the written plan (7 decimals) | previews only; one line moves by 1 px at one place (29 px on the golden region) |
| 2026-10-13 | waterfall preview | `mods/dhe_waterfall_preview_2x2.png` next to the climate mods | `<project>_waterfall_preview.png` in the project folder | the run writes into the project folder only; same pixels |
| 2026-10-13 | the map's own mods (roads, path streets, industry names) | `<project>/mods/` in the project folder | `<output>/mods/` next to the climate mod | one mods folder per run; same files |
| 2026-10-14 | painted rivers | on the OSM course | moved into the valley line (`rivers.to_valley`, ADR 0011) | beds lay on the slope; the golden-master project sets `max_move_real_m = 0`, so the comparison is unchanged |
| 2026-10-17 | road network plan and road mod (`map_roads`) | sections with gaps at branches, heights from the terrain in game | ways share their nodes (junctions), every node has its game height, ways of kind normal/bridge/tunnel (ADR 0016) | redesign; `_roads_plan.json/.png` and the roads mod are not compared (`tests/comparison/legacy.py`, `NOT_COMPARED`, `NOT_COMPARED_MODS`); since 2026-10-18 the comparison runs `map` and `map_parcels` (no road network), so the path street script (legacy: gaps at the legacy roads) is not compared either |
| 2026-10-17 | preset Alpine Realistic, surface `alpine_ground` | base `gr_br_sub`, patches `alpine_grass` (`grass_alpine`) and `alpine_rock` (`rock`); south look changes only the patches | base `grass_alpine`, patches `subarctic_grass` (`gr_br_sub`) and `ground_scree` (`scree`); south look (`brenner_north_south`) also makes the base `grass_brown` | alpine grass as ground; the comparisons set the legacy look back (`tests/comparison/test_legacy_climate.py` `_legacy_defaults`, `configs/projects/golden_test.toml`), everything else byte-equal |
| 2026-10-14 | climate definition `*.clima.lua`, import layers | layer names from the preset's `label` (German, e.g. "Wald"); original layers "Mountains", "Rivers" | every layer named by its key = suffix of its mask file (`forest` <-> `<map>_forest.png`, also `mountains`, `rivers`) | the import dialog lists the layers sorted by key, the file dialog the files alphabetically - the same names and order make the mapping easy; the comparison renames the legacy layers by key, everything else byte-equal |
| 2026-10-18 | climate mod without transition: original layer `rivers` | kept in the import dialog (slot 0), read by the base climate's river trees | left out; its consumers in the import tree read an empty map (`neutralize_unused_original_layers`) | the layer is not used (no `<map>_rivers.png`); maps with a transition left it out before; the comparisons keep it (`golden_test.toml`, `_legacy_defaults`) |
