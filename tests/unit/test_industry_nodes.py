"""Industry nodes on synthetic data: rules from game files, standard buildings only, a small planning run."""

from __future__ import annotations

import tomllib
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.crs import CRS

from automapgen.graph import NodeContext
from automapgen.nodes import industries
from automapgen.nodes.images import Image
from automapgen.nodes.industries import IndustryParams, IndustryRules, IndustrySettings
from automapgen.nodes.maps import MapFormat, MapFrame, Rotation, SourceBounds
from automapgen.nodes.osm import OsmPoints
from automapgen.nodes.rasters import Field, Mask
from automapgen.nodes.towns import TownList

ROOT = Path(__file__).resolve().parents[2]
LOG: list[str] = []
SETTINGS = IndustrySettings.model_validate(
    tomllib.loads((ROOT / "configs" / "industries" / "temperate.toml").read_text(encoding="utf-8"))
    ["defaults"]["industries"])
CONTEXT = NodeContext("industries", LOG.append, lambda f: None, lambda: False)
_NAMES = sorted(set(SETTINGS.plan.raw_resource) | set(SETTINGS.plan.intermediate) | set(SETTINGS.plan.complex))
_CARGO = dict.fromkeys(SETTINGS.plan.town_cargos, 1.0)
CON_LUA = '''function data()
local stockListConfig = {
\t{ type = "INPUT_STOCK", cargoType = "::/cargos/logs/logs.cargo", },
\t{ type = "OUTPUT_STOCK", cargoType = "::/cargos/planks/planks.cargo", },
}
local config = {
\trules = {
\t\t\t{
\t\t\t\tinput = { { 2, }, },
\t\t\t\toutput = { ["::/cargos/planks/planks.cargo"] = 1, },
\t\t\t\tcapacity = 20,
\t\t\t}, {
\t\t\t\tinput = { { 9, }, },
\t\t\t\toutput = { ["::/cargos/planks/planks.cargo"] = 9, },
\t\t\t\tcapacity = 99,
\t\t\t},
\t},
}
industrialCapacity = 400
end
'''

FOOD_CON_LUA = '''function data()
local stockListConfig = {
	{ type = "INPUT_STOCK", cargoType = "::/cargos/fish/fish.cargo", },
	{ type = "INPUT_STOCK", cargoType = "::/cargos/meat/meat.cargo", },
	{ type = "OUTPUT_STOCK", cargoType = "::/cargos/tinned_food/tinned_food.cargo", },
}
local config = {
	rules = {
			{
				input = { { 2, 0 } },
				output = { ["::/cargos/tinned_food/tinned_food.cargo"] = 2 },
				capacity = 45,
			},{
				input = { { 0, 2 } },
				output = { ["::/cargos/tinned_food/tinned_food.cargo"] = 2 },
				capacity = 45,
			},
	},
}
industrialCapacity = 20
end
'''


def test_industry_rules_from_the_game_files(tmp_path: Path) -> None:
    folder = tmp_path / "base" / "content" / "industries"
    folder.mkdir(parents=True)
    with zipfile.ZipFile(folder / "saw_mill.zip", "w") as z:
        z.writestr("saw_mill/saw_mill.con.lua", CON_LUA)
    with zipfile.ZipFile(folder / "food_factory.zip", "w") as z:          # second rule: same output, other input
        z.writestr("food_factory/food_factory.con.lua", FOOD_CON_LUA)
    with zipfile.ZipFile(folder / "shared.zip", "w") as z:
        z.writestr("shared/readme.txt", "no industry")
    context = NodeContext("rules", LOG.append, lambda f: None, lambda: False, game=tmp_path)

    rules = industries.industry_rules(context)

    assert rules.rule("saw_mill") == ({"logs": 2.0}, {"planks": 1.0}, 20.0)    # the first rule only
    assert rules.input_choices("saw_mill") == [{"logs": 2.0}]                  # the booster has another output
    assert rules.rule("food_factory") == ({"fish": 2.0}, {"tinned_food": 2.0}, 45.0)
    assert rules.input_choices("food_factory") == [{"fish": 2.0}, {"meat": 2.0}]
    assert sorted(rules.data) == ["food_factory", "saw_mill"]


def test_standard_buildings_only() -> None:
    effective = SETTINGS.effective()

    assert "plantation" not in effective.sites.resources                       # rubber farm is a mod building
    assert all("compact" not in r.sites for r in effective.sites.resources.values())
    assert "cement" not in effective.plan.town_cargos
    assert effective.plan.trade_points == 0
    assert SETTINGS.model_copy(update={"standard_only": False}).effective().sites.resources["coal"].sites == (
        "field", "compact")


def _frame(n: int) -> MapFrame:
    return MapFrame(n, n, SourceBounds(11.0, 47.0, 11.0424, 47.0288, True), Rotation.NORTH,
                    CRS.from_epsg(4326).to_wkt())


def _areas_synthetic(towns: TownList, settings: IndustrySettings = SETTINGS, **options: Any) -> industries.Areas:
    """3.2 x 3.2 km at 600 m (scale 1:1): cropland in the left half, forest in the right half."""
    n = 801
    frame = _frame(n)
    _, xx = np.mgrid[0:n, 0:n]
    heights = Field(np.full((n, n), 600, dtype=np.float32) + 0.01 * xx, frame)
    empty = Mask(np.zeros((n, n), dtype=bool), frame)
    left, right = Mask(xx < n // 2, frame), Mask(xx >= n // 2, frame)
    areas = industries.resource_areas(heights, Image(np.full((n, n), 1000, dtype=np.uint16)), 500.0,
                                      MapFormat(3.2, 3.2, 1, 1), left, empty, right, empty, empty, empty, empty, empty,
                                      towns, IndustryParams(industries=settings, **options), CONTEXT)
    assert areas.grid.shape == (201, 201)
    assert areas.resources_preview.pixels.ndim == 3
    return areas


def _plan_synthetic(towns: TownList, factories_make_cargo: bool = True, rules: IndustryRules | None = None,
                    settings: IndustrySettings = SETTINGS, **options: Any) -> industries.Industries:
    """Every industry makes every town cargo; without `factories_make_cargo` only the raw-material ones; `rules`
    replace these synthetic rules."""
    rules = rules or IndustryRules({n: [{}, _CARGO if factories_make_cargo or n in SETTINGS.plan.raw_resource else {},
                                        10.0] for n in _NAMES})
    areas = _areas_synthetic(towns, settings, **options)
    return industries.plan(areas.grid, areas.maps, rules, OsmPoints(()), _frame(801),
                           IndustryParams(industries=settings, **options), CONTEXT)


def _settings(sites: dict[str, Any] | None = None, plan: dict[str, Any] | None = None,
              **resources: dict[str, Any]) -> IndustrySettings:
    """SETTINGS with changed site and plan values and changed resource specs."""
    specs = {**SETTINGS.sites.resources,
             **{k: SETTINGS.sites.resources[k].model_copy(update=v) for k, v in resources.items()}}
    return SETTINGS.model_copy(update={
        "sites": SETTINGS.sites.model_copy(update={**(sites or {}), "resources": specs}),
        "plan": SETTINGS.plan.model_copy(update=plan or {})})


def _region_count(areas: industries.Areas, key: str) -> int:
    return int(areas.maps.regions[key].max())


def _types(planned: industries.Industries) -> list[str]:
    return [q["type"] for q in planned.industries.data]


def test_planning_on_a_synthetic_map() -> None:
    planned = _plan_synthetic(TownList([{"name": "Middle", "x": 0.0, "y": 0.0, "capacity": 300}]))

    assert planned.industries.data, "at least one industry"
    assert all(q["name"].startswith("Middle ") for q in planned.industries.data)   # named after the only town
    assert "fileName" in planned.lua.text
    assert "fileName" not in planned.towns_lua.text                                 # towns with demand only
    assert 'name = "Middle"' in planned.towns_lua.text


def test_equal_towns_get_factories_and_a_long_distance_cargo() -> None:
    small = TownList([{"name": "Middle", "x": 0.0, "y": 0.0, "capacity": 45}])

    normal = _plan_synthetic(small)
    equal = _plan_synthetic(small, ignore_town_size=True)
    normal_demand = _plan_synthetic(small, factories_make_cargo=False)
    equal_demand = _plan_synthetic(small, factories_make_cargo=False, ignore_town_size=True)

    processing = set(SETTINGS.plan.intermediate) | set(SETTINGS.plan.complex)
    assert not {q["type"] for q in normal.industries.data} & processing      # a village gets no factory
    assert len({q["type"] for q in equal.industries.data} & processing) == SETTINGS.plan.equal_town_slots
    assert not any(f'"{g}"' in normal_demand.towns_lua.text for g in SETTINGS.plan.far_cargos)
    assert any(f'"{g}"' in equal_demand.towns_lua.text for g in SETTINGS.plan.far_cargos)


def test_height_band_from_the_resource_spec() -> None:
    town = TownList([{"name": "Middle", "x": 0.0, "y": 0.0, "capacity": 300}])

    assert _region_count(_areas_synthetic(town, _settings(farm={"height_m": (0, 0, 750, 1100)})), "farm") > 0
    assert _region_count(_areas_synthetic(town, _settings(farm={"height_m": (700, 800, 1e6, 1e6)})), "farm") == 0


def test_industry_density_scales_areas_and_factories() -> None:
    towns = TownList([{"name": f"Town {k}", "x": x, "y": 0.0, "capacity": 300} for k, x in enumerate((-1000, 1000))])

    small_areas = _settings(sites={"region_min_km2": 0.1},          # room for many areas on the small map
                            forest={"regions": 2, "radius_game_m": 300, "spacing_game_m": 500})
    one_type = _settings(plan={"intermediate": ("glass_works",), "complex": ()})      # two medium towns, one slot each

    assert _region_count(_areas_synthetic(towns, small_areas), "forest") == 2
    assert _region_count(_areas_synthetic(towns, small_areas, industry_density=2), "forest") == 4
    assert _types(_plan_synthetic(towns, settings=one_type)).count("glass_works") == 1
    assert _types(_plan_synthetic(towns, settings=one_type, industry_density=2)).count("glass_works") == 2


def test_all_cargos_gives_areas_without_fitting_land() -> None:
    town = TownList([{"name": "Middle", "x": 0.0, "y": 0.0, "capacity": 300}])

    assert _region_count(_areas_synthetic(town), "stone") == 0              # no rock
    assert _region_count(_areas_synthetic(town, all_cargos=True), "stone") >= 1
    assert _region_count(_areas_synthetic(town, all_cargos=True), "fish") == 0      # no real water
    assert "quarry" in _types(_plan_synthetic(town, all_cargos=True))


def test_industries_without_input_source_are_skipped() -> None:
    towns = TownList([{"name": f"Town {k}", "x": x, "y": 0.0, "capacity": 300} for k, x in enumerate((-1000, 1000))])
    steel_only = _settings(plan={"intermediate": ("steel_mill",), "complex": ("machine_factory",), "trade_points": 0})
    inputs = {"steel_mill": {"coal": 1.0, "iron_ore": 1.0}, "machine_factory": {"steel": 1.0}}
    outputs = {"coal_mine": {"coal": 1.0}, "iron_ore_mine": {"iron_ore": 1.0}, "steel_mill": {"steel": 1.0},
               "machine_factory": {"machines": 1.0}}
    rules = IndustryRules({n: [inputs.get(n, {}), {**_CARGO, **outputs.get(n, {})}, 10.0] for n in _NAMES})

    strict = _types(_plan_synthetic(towns, rules=rules, settings=steel_only))
    anyway = _types(_plan_synthetic(towns, rules=rules, settings=steel_only, all_cargos=True))

    assert "iron_ore_mine" not in strict                                     # no mountains
    assert "steel_mill" not in strict
    assert "machine_factory" not in strict                                  # its steel has no source either
    assert {"steel_mill", "machine_factory"} <= set(anyway)


def test_an_alternative_rule_supplies_a_factory() -> None:
    town = TownList([{"name": "Middle", "x": 0.0, "y": 0.0, "capacity": 300}])
    food_only = _settings(plan={"intermediate": (), "complex": ("food_factory",), "trade_points": 0})
    other = {g: a for g, a in _CARGO.items() if g != "fish"}           # only the fishing grounds make fish
    food = [{"fish": 2.0}, other, 10.0]

    def rules(alternatives: list[dict[str, float]]) -> IndustryRules:
        return IndustryRules({n: [*food, alternatives] if n == "food_factory"
                              else [{}, _CARGO if n == "fishing_grounds" else other, 10.0] for n in _NAMES})

    assert "food_factory" not in _types(_plan_synthetic(town, rules=rules([]), settings=food_only))   # no water
    assert "food_factory" in _types(_plan_synthetic(town, rules=rules([{"meat": 2.0}]), settings=food_only))


def test_raw_materials_without_a_customer_are_skipped() -> None:
    town = TownList([{"name": "Middle", "x": 0.0, "y": 0.0, "capacity": 300}])
    rules = IndustryRules({n: [{}, {"stone": 1.0} if n == "quarry" else _CARGO, 10.0] for n in _NAMES})
    used = IndustryRules({n: [{"stone": 1.0} if n == "glass_works" else {}, rules.data[n][1], 10.0] for n in _NAMES})

    assert "quarry" not in _types(_plan_synthetic(town, rules=rules, all_cargos=True))
    assert "quarry" in _types(_plan_synthetic(town, rules=used, all_cargos=True))
