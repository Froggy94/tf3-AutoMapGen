"""The map's own mods: names, road mod, path street mod, industry names mod, templates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from automapgen.graph import NodeContext
from automapgen.graph.paramsets import ParameterSet
from automapgen.nodes.industries import IndustrySettings
from automapgen.nodes.lines import PathPlan
from automapgen.nodes.map_mods import industry_names, info, path_streets, road_test, roads
from automapgen.nodes.map_mods.roads import RoadEdgeSplitter
from automapgen.nodes.map_mods.template import LuaTemplate
from automapgen.nodes.maps import MapFormat
from automapgen.nodes.mods import PublishingSettings
from automapgen.nodes.roads.plan import RoadPlan
from automapgen.nodes.towns import TownList

PRESET = ParameterSet.load(Path(__file__).resolve().parents[2] / "configs" / "presets" / "alpine_realistic.toml")
PUBLISHING = PublishingSettings.model_validate(PRESET.defaults["publishing"])
TEXTS = {kind: info.MapModText.model_validate(t) for kind, t in PRESET.defaults["map_mod_texts"].items()}
FORMAT = MapFormat(2.048, 1.024, 2, 2)                     # 2048 x 1024 game metres
LOG: list[str] = []
CONTEXT = NodeContext("mod", LOG.append, lambda f: None, lambda: False)


def _info(kind: str) -> info.MapModInfo:
    return info.map_info(info.MapModInfoParams(kind=kind, map_name="Brenner", publishing=PUBLISHING,
                                               map_mod_texts=TEXTS))


def test_map_mod_names() -> None:
    names = _info("industry_names")

    assert (names.folder, names.mod_id) == ("DHE_brenner_industry_names", "DHE_brenner_industry_names_1")
    assert names.info.data["name"] == "DHE Industry Names Brenner"
    assert "{map}" not in names.info.data["description"]
    assert "Brenner" in names.info.data["description"]
    with pytest.raises(ValueError, match="no texts"):
        _info("unknown")


BRIDGES = ((120.0, "/trestle.bridge"), (1e9, "/steel.bridge"))


def _normal(*nodes: int) -> dict[str, Any]:
    return {"kind": "normal", "nodes": list(nodes)}


def test_road_pieces_are_merged_and_split() -> None:
    # 1000 m wide: 0.001 = 1 m; the middle point 5 m from the start is merged, the 95 m piece split into 3
    nodes = [[0.0, 0.0, 0.0], [0.005, 0.0, 1.0], [0.1, 0.0, 9.0]]

    split = RoadEdgeSplitter(8, 40, 1000, 1000, BRIDGES).split(nodes, [_normal(0, 1, 2)])

    assert len(split.ways) == 1
    assert split.edge_count == 3
    assert split.length_game_m == pytest.approx(100)
    assert [split.points[i][2] for i in split.ways[0].nodes] == pytest.approx([0, 3, 6, 9])   # heights linear


def test_road_ways_share_their_end_nodes() -> None:
    nodes = [[0.1, 0.1, 0.0], [0.5, 0.1, 0.0], [0.5, 0.9, 0.0], [0.9, 0.1, 0.0]]
    ways = [_normal(0, 1), _normal(1, 2), {"kind": "bridge", "nodes": [1, 3]}, {"kind": "tunnel", "nodes": [2, 3]}]

    split = RoadEdgeSplitter(8, 1000, 1000, 1000, BRIDGES).split(nodes, ways)

    first, second, bridge, tunnel = split.ways
    assert first.nodes[-1] == second.nodes[0] == bridge.nodes[0]
    assert split.junction_count == 1                                    # three ways meet at node 1
    assert (first.kind, bridge.kind, tunnel.kind) == (0, 1, 2)
    assert (first.structure, bridge.structure, tunnel.structure) == (0, 2, 3)   # 400 m: steel; tunnel after bridges


def test_road_mod_writes_the_script() -> None:
    plan = RoadPlan({"targets": [], "nodes": [[0.1, 0.1, 5.0], [0.5, 0.1, 6.0], [0.5, 0.9, 7.5]],
                     "ways": [_normal(0, 1), {"kind": "bridge", "nodes": [1, 2]}]})
    settings = roads.RoadModSettings.model_validate(PRESET.defaults["road_mod"])

    files = dict(roads.road_mod(plan, FORMAT, roads.RoadModParams(road_mod=settings), CONTEXT).files)
    script = files["content/dhe_roads/dhe_roads.script.lua"].decode()

    assert sorted(files) == ["content/dhe_roads/dhe_roads.gs.lua", "content/dhe_roads/dhe_roads.script.lua"]
    assert "aspect = 2.000000" in script
    assert 'template = "street/country/country_new_small.street_template"' in script
    assert 'structures = { "/trestle.bridge", "/steel.bridge", "/tunnel_a_car.tunnel", },' in script
    assert 'nodes = "1000000 1000000 500 ' in script                                   # u, v x 1e7; height in cm
    assert " 5000000 1000000 600 " in script
    assert "@" not in script.split("local WAIT_TICKS")[0]
    with pytest.raises(ValueError, match="road_era"):
        roads.road_mod(plan, FORMAT, roads.RoadModParams(road_mod=settings.model_copy(update={"road_era": "x"})),
                       CONTEXT)


def test_path_street_mod_builds_crossing_paths() -> None:
    settings = path_streets.PathStreetModSettings.model_validate(PRESET.defaults["path_street_mod"])
    paths = PathPlan({"track": [[[0.1, 0.5], [0.9, 0.5]]], "trail": [[[0.5, 0.1], [0.5, 0.9]]]})
    mod = path_streets.path_street_mod(paths, FORMAT, "M_1", path_streets.PathStreetModParams(path_street_mod=settings),
                                       CONTEXT)
    files = dict(mod.files.files)
    script = files["content/dhe_path_streets/dhe_path_streets.script.lua"].decode()

    assert "content/infrastructure/street/dhe/dhe_track.street_template.lua" in files
    assert "content/infrastructure/street/dhe/dhe_trail_fill.gtex.lua" in files
    assert 'modId = "M_1",' in script
    assert mod.translations.data["Field track (Alps)"] == "Feldweg (Alpen)"
    assert settings.test_area is None


def test_path_street_mod_leaves_gaps_near_planned_roads() -> None:
    settings = path_streets.PathStreetModSettings.model_validate(PRESET.defaults["path_street_mod"])
    paths = PathPlan({"track": [[[0.1, 0.5], [0.9, 0.5]]]})
    road = RoadPlan({"targets": [], "nodes": [[0.5, 0.1, 0.0], [0.5, 0.9, 0.0]], "ways": [_normal(0, 1)]})
    params = path_streets.PathStreetModParams(path_street_mod=settings)

    def script(roads: RoadPlan | None) -> str:
        files = dict(path_streets.path_street_mod(paths, FORMAT, "M_1", params, CONTEXT, roads).files.files)
        return files["content/dhe_path_streets/dhe_path_streets.script.lua"].decode()

    assert script(None) == script(RoadPlan({"targets": [], "nodes": [], "ways": []}))
    assert script(road) != script(None)


def test_industry_names_mod_lists_towns_and_types() -> None:
    settings = industry_names.IndustryNamesModSettings.model_validate(PRESET.defaults["industry_names_mod"])
    industries = IndustrySettings.model_validate(PRESET.defaults["industries"])
    towns = TownList([{"name": 'Matrei "am" Brenner', "x": -112, "y": 368.04, "capacity": 100}])

    files = dict(industry_names.industry_names_mod(towns, industry_names.IndustryNamesModParams(
        industry_names_mod=settings, industries=industries), CONTEXT).files)
    script = files["content/dhe_industry_names/dhe_industry_names.script.lua"].decode()

    assert '\t{ -112.0, 368.0, "Matrei \\"am\\" Brenner" },' in script
    assert '\tfarm = "Bauernhof",' in script


def test_template_needs_every_placeholder() -> None:
    template = LuaTemplate("a = @A@, b = @B@")

    assert template.render({"A": "1", "B": "2"}) == "a = 1, b = 2"
    with pytest.raises(ValueError, match=r"without value \['B'\]"):
        template.render({"A": "1"})
    with pytest.raises(ValueError, match=r"without placeholder \['C'\]"):
        template.render({"A": "1", "B": "2", "C": "3"})


def test_road_test_mod_writes_its_settings_into_the_script() -> None:
    settings = ParameterSet.load(Path(__file__).resolve().parents[2] / "configs" / "road_test.toml").defaults
    params = road_test.RoadTestParams(road_test=road_test.RoadTestSettings.model_validate(settings["road_test"]))

    files = dict(road_test.road_test_mod(params).files)

    script = files["content/dhe_road_test/dhe_road_test.script.lua"].decode("utf-8")
    assert "radii = { 8, 12, 16, 20, 30 }," in script
    assert '"tunnel_a_car.lua"' in script
    assert "@" not in script.split("local DATA")[1].split("}")[0]
    assert b"dhe_road_test.script@update" in files["content/dhe_road_test/dhe_road_test.gs.lua"]
