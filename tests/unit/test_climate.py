"""The climate preset (switching parts off, consistency), the landscape with a transition, and the small building blocks
of the climate's trees. The trees themselves are compared with the legacy project (needs the game's files)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from rasterio.crs import CRS

from automapgen.graph import NodeContext, merged_tables
from automapgen.graph.paramsets import ParameterSet
from automapgen.lua import DataDocument, LuaCall, LuaTable, NodeTree, Port
from automapgen.nodes.climate import package, zones
from automapgen.nodes.climate.areas import AreaBuilder
from automapgen.nodes.climate.definition import ClimaDefinitionEditor, ClimateIdentity
from automapgen.nodes.climate.factory import NodeFactory
from automapgen.nodes.climate.heights import GameHeights
from automapgen.nodes.climate.material_tree import MaterialPaths, SecondaryOrders
from automapgen.nodes.climate.preset import (
    ClimatePreset,
    GameMaterial,
    Landscape,
    LayerArea,
    LayerSpec,
    ModMaterial,
    SlotPlan,
    Transition,
)
from automapgen.nodes.maps import MapFrame, Rotation, SourceBounds

CONFIGS = Path(__file__).resolve().parents[2] / "configs"


def _defaults(name: str) -> dict[str, Any]:
    return dict(ParameterSet.load(CONFIGS / name).defaults)


def _preset(changes: dict[str, Any] | None = None) -> ClimatePreset:
    return ClimatePreset.model_validate(merged_tables(_defaults("presets/alpine_realistic.toml")["climate"],
                                                      changes or {}))


def test_preset_parts_are_switched_off_by_enabled_false() -> None:
    preset = _preset({"rocks": {"waterfall": {"enabled": False}},
                      "surfaces": {"shore": {"enabled": False},
                                   "meadow": {"look": {"patches": {"meadow_patch": {"enabled": False}}}}}})

    assert preset.forest.classic is None                       # enabled = false in the file, values kept there
    assert preset.rocks.waterfall is None
    assert "shore" not in preset.surfaces
    assert preset.surfaces["meadow"].look.patches == {}
    assert list(preset.surfaces)[:3] == ["meadow", "alpine_grass", "alpine_ground"]   # painted in file order


def test_preset_problems() -> None:
    with pytest.raises(ValidationError, match="surfaces use layers without slot"):
        _preset({"surfaces": {"field": {"area": {"kind": "layer", "key": "vineyard"}}}})
    with pytest.raises(ValidationError, match="no surface 'water' with a steep water area"):
        _preset({"rocks": {"waterfall": {"surface": "water"}}})
    layer = {"color": [0, 0, 0], "slot": 1, "resolution": 2}
    with pytest.raises(ValidationError, match=r"used twice(.|\n)*finer than 4 m(.|\n)*need the layer cropland"):
        SlotPlan.model_validate({"layers": {"crop_corn": layer}, "original": {"mountains": 1}})


def test_transition_secondary_is_the_primary_with_differences() -> None:
    landscape_file = _defaults("landscapes/brenner_north_south.toml")
    primary = ClimatePreset.model_validate(landscape_file["climate"])

    landscape = Landscape(primary, Transition.model_validate(landscape_file["transition"]))

    assert landscape.name == "Alpine North-South"
    assert landscape.primary.slots.original == {"mountains": 1}            # the mask uses the free slot 0
    assert list(landscape.primary.slots.layers)[-1] == "transition_mask"
    assert landscape.secondary is not None
    south = landscape.secondary.surfaces
    assert south["meadow"].look.patches == {}
    assert south["rock"].look.base == GameMaterial(name="rock_dry")
    assert south["shore"] == primary.surfaces["shore"]                      # everything else as in the north
    assert Landscape(primary).secondary is None


def test_transition_problems() -> None:
    transition = _defaults("landscapes/brenner_north_south.toml")["transition"]
    primary = _preset()

    on_mountains = {**transition, "layer": {**transition["layer"], "slot": 1}}
    with pytest.raises(ValueError, match="slots are used twice"):
        Landscape(primary, Transition.model_validate(on_mountains))
    desert = {"desert": {"factor": {"default": 1}}}
    with pytest.raises(ValueError, match="must exist in both presets"):
        Landscape(primary, Transition.model_validate({**transition, "surfaces": desert}))


def test_game_heights_and_material_paths() -> None:
    assert GameHeights(540, 0.5).of(2100) == 780.0
    assert MaterialPaths("MOD").of(GameMaterial(name="rock")) == "::/terrain/materials/rock/rock.tmat"
    assert MaterialPaths("MOD").of(ModMaterial(folder="w", file="w_a")) == "MOD::/terrain/materials/w/w_a.tmat"


def test_climate_reference_is_the_lowest_land_rounded_to_the_step() -> None:
    rounded = package.ReferenceParams()
    assert [package.reference(m, rounded) for m in (549.9, 550, 560, 649.9)] == [500, 600, 600, 600]
    assert package.reference(547.3, package.ReferenceParams(step_m=0)) == 547.3
    assert package.reference(12, package.ReferenceParams(step_m=25)) == 0


def test_secondary_orders() -> None:
    assert SecondaryOrders(on_top=True, dropped=[6]).of(5, 5) == 7       # above the counterpart and dropped patches
    assert SecondaryOrders(on_top=True, dropped=[]).of(900, 5) == 900     # never below its own order
    assert SecondaryOrders(on_top=False, dropped=[6]).of(5, 5) == 5       # side by side: own order


class _NoGameAreas:
    slope = Port("slope")


def test_areas_are_built_once_and_hard_edges_cut_the_layer() -> None:
    tree = NodeTree(DataDocument.parse("function data() return { nodes = { } } end"))
    factory = NodeFactory(tree, (0, 0))
    areas = AreaBuilder(factory, _NoGameAreas(), {"water": Port("water map", "input")}, {})  # type: ignore[arg-type]

    hard = areas.port(LayerArea(key="water", hard=True), "water")

    assert areas.port(LayerArea(key="water", hard=True), "again") == hard
    assert areas.port(LayerArea(key="water"), "soft") == Port("water map", "input")
    assert [node.name for node in tree] == ["lc water hard"]
    assert tree.node("lc water hard").inputs() == {"in1": Port("water map", "input")}


def test_transition_mask_marks_where_the_transition_has_begun() -> None:
    transition = Transition.model_validate(_defaults("landscapes/brenner_north_south.toml")["transition"])
    zone = transition.zone.model_copy(update={"start_lat": 47.5, "end_lat": 46.7})    # north -> south
    params = zones.TransitionMaskParams(transition=transition.model_copy(update={"zone": zone}))
    context = NodeContext("mask", lambda m: None, lambda f: None, lambda: False)
    north_up = MapFrame(4, 10, SourceBounds(11.0, 47.0, 11.1, 48.0, True), Rotation.NORTH,
                        CRS.from_epsg(4326).to_wkt())
    east_up = MapFrame(10, 4, north_up.bounds, Rotation.EAST, north_up.crs_wkt)

    mask = zones.transition_mask(north_up, params, context).values
    turned = zones.transition_mask(east_up, params, context).values

    assert mask[:, 0].tolist() == [False] * 5 + [True] * 5                 # the southern half
    assert turned[0].tolist() == [False] * 5 + [True] * 5                   # top = east: north on the left
    assert not zones.transition_mask(north_up, zones.TransitionMaskParams(), context).values.any()


def test_import_layers_are_named_and_sorted_like_their_mask_files() -> None:
    clima = DataDocument.parse("""function data() return {
        environment = "temperate.env", economy = "::/economy/temperate.eco", desc = { },
        mapColoring = { levels = { { height = 0 }, { height = 1 }, { height = 2 }, { height = 3 } } },
        dataMaps = { layers = {
            { key = "rivers", displayName = _("Rivers"), id = 0 },
            { key = "mountains", displayName = _("Mountains"), id = 1 } } } } end""")
    own = {key: LayerSpec(color=(0, 0, 0), slot=slot, resolution=8)
           for key, slot in (("forest", 2), ("crop_corn", 3), ("cropland", 4))}
    identity = ClimateIdentity("DHE-X", "DHE X", "", "temperate")

    root = ClimaDefinitionEditor(clima, identity).apply(own, None, (1, 2), {"mountains": 1}).root

    layers = [entry for entry in root.table("dataMaps").table("layers").array() if isinstance(entry, LuaTable)]
    assert [str(entry["key"]) for entry in layers] == ["crop_corn", "cropland", "forest", "mountains"]
    assert all(entry["displayName"] == LuaCall("_", (entry["key"],)) for entry in layers)
