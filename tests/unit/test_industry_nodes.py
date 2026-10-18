"""Industry nodes on synthetic data: rules from game files, standard buildings only, a small planning run."""

from __future__ import annotations

import tomllib
import zipfile
from pathlib import Path

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


def test_industry_rules_from_the_game_files(tmp_path: Path) -> None:
    folder = tmp_path / "base" / "content" / "industries"
    folder.mkdir(parents=True)
    with zipfile.ZipFile(folder / "saw_mill.zip", "w") as z:
        z.writestr("saw_mill/saw_mill.con.lua", CON_LUA)
    with zipfile.ZipFile(folder / "shared.zip", "w") as z:
        z.writestr("shared/readme.txt", "no industry")
    context = NodeContext("rules", LOG.append, lambda f: None, lambda: False, game=tmp_path)

    rules = industries.industry_rules(context)

    assert rules.rule("saw_mill") == ({"logs": 2.0}, {"planks": 1.0}, 20.0)    # the first rule only
    assert list(rules.data) == ["saw_mill"]


def test_standard_buildings_only() -> None:
    effective = SETTINGS.effective()

    assert "plantation" not in effective.sites.resources                       # rubber farm is a mod building
    assert all("compact" not in r.sites for r in effective.sites.resources.values())
    assert "cement" not in effective.plan.town_cargos
    assert effective.plan.trade_points == 0
    assert SETTINGS.model_copy(update={"standard_only": False}).effective().sites.resources["coal"].sites == (
        "field", "compact")


def test_planning_on_a_synthetic_map() -> None:
    n = 801                                                     # 3.2 x 3.2 km at scale 1:1
    frame = MapFrame(n, n, SourceBounds(11.0, 47.0, 11.0424, 47.0288, True), Rotation.NORTH,
                     CRS.from_epsg(4326).to_wkt())
    _, xx = np.mgrid[0:n, 0:n]
    heights = Field(np.full((n, n), 600, dtype=np.float32) + 0.01 * xx, frame)
    empty = Mask(np.zeros((n, n), dtype=bool), frame)
    left, right = Mask(xx < n // 2, frame), Mask(xx >= n // 2, frame)
    towns = TownList([{"name": "Middle", "x": 0.0, "y": 0.0, "capacity": 300}])
    names = sorted(set(SETTINGS.plan.raw_resource) | set(SETTINGS.plan.intermediate) | set(SETTINGS.plan.complex))
    rules = IndustryRules({n: [{}, dict.fromkeys(SETTINGS.plan.town_cargos, 1.0), 10.0] for n in names})
    params = IndustryParams(industries=SETTINGS)
    context = NodeContext("industries", LOG.append, lambda f: None, lambda: False)

    areas = industries.resource_areas(heights, Image(np.full((n, n), 1000, dtype=np.uint16)), 500.0,
                                      MapFormat(3.2, 3.2, 1), left, empty, right, empty, empty, empty, empty, empty,
                                      towns, params, context)
    planned = industries.plan(areas.grid, areas.maps, rules, OsmPoints(()), frame, params, context)

    assert areas.grid.shape == (201, 201)
    assert areas.resources_preview.pixels.ndim == 3
    assert planned.industries.data, "at least one industry"
    assert all(q["name"].startswith("Middle ") for q in planned.industries.data)   # named after the only town
    assert "fileName" in planned.lua.text
    assert "fileName" not in planned.towns_lua.text                                 # towns with demand only
    assert 'name = "Middle"' in planned.towns_lua.text
