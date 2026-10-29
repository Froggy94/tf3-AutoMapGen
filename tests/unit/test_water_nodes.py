"""Water nodes on small synthetic maps: river plan, water shaping, water layers, riverbed, groove, helpers."""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from rasterio.crs import CRS

from automapgen.graph import NodeContext
from automapgen.nodes import fields, masks, river_valley, riverbed, rivers, water
from automapgen.nodes.maps import MapFormat, MapFrame, Rotation, SourceBounds
from automapgen.nodes.osm import OsmWays, Way
from automapgen.nodes.rasters import ClassMap, Field, Mask
from automapgen.nodes.water import BedSegment, BedSegments

LOG: list[str] = []
CONTEXT = NodeContext("test", LOG.append, lambda f: None, lambda: False)
# 101 x 101 px heightmap over 0.01 x 0.005 degrees at 47° N (about 760 x 556 m): roughly 7.6 x 5.6 m per pixel
FRAME = MapFrame(101, 101, SourceBounds(11.0, 47.0, 11.01, 47.005, True), Rotation.NORTH, CRS.from_epsg(4326).to_wkt())
FORMAT = MapFormat(0.4, 0.4, 2, 2)


def test_helper_nodes() -> None:
    dem = Field(np.add.outer(np.arange(11.0), np.arange(11.0)), FRAME)
    dot = Mask(np.zeros((11, 11), dtype=bool), FRAME).like(np.pad(np.ones((1, 1), dtype=bool), 5))

    assert fields.min_within(dem, fields.RadiusParams(radius_px=2)).values[5, 5] == 8   # 10 - 2 in the steepest dir.
    assert int(masks.near(dot, masks.NearParams(distance_px=1)).values.sum()) == 5
    assert not masks.near(dot.like(np.zeros((11, 11), dtype=bool)), masks.NearParams(distance_px=3)).values.any()
    assert not masks.subtract(dot, (dot,)).values.any()


def _river_ways() -> OsmWays:
    """A river flowing west -> east along the middle of the map."""
    nodes = tuple((i, 11.0 + 0.001 * i, 47.0025) for i in range(11))
    return OsmWays((Way(1, "Test River", "river", "", nodes),))


def _canal_and_river() -> OsmWays:
    """A navigable canal (CEMT class) west -> east along the middle, a river joining it from the north at point 5."""
    canal = tuple((i, 11.0 + 0.001 * i, 47.0025) for i in range(11))
    river = (*((100 + i, 11.005, 47.0049 - 0.0004 * i) for i in range(6)), canal[5])
    return OsmWays((Way(1, "Canal", "canal", "Vb", canal), Way(2, "Brook", "river", "", river)))


def _section(plan: rivers.RiverPlan, name: str) -> dict[str, Any]:
    return next(r for r in plan.data["rivers"] if r["name"] == name)


def test_river_plan_real_downstream_painted_upstream() -> None:
    # valley floor falls from 600 m in the west to 540 m in the east; real water up to 550 m
    floor = Field(np.tile(np.linspace(600, 540, 101, dtype=np.float32), (101, 1)), FRAME)
    no_water = Mask(np.zeros((101, 101), dtype=bool), FRAME)
    params = rivers.RiverParams(real_water_until_m=550, min_real_run_game_m=0, floor_smooth_passes=0,
                                preview_width=200)

    result = rivers.plan(_river_ways(), FRAME, floor, floor, no_water, 540.0, params, CONTEXT)

    section = result.plan.data["rivers"][0]
    assert section["name"] == "Test River"
    assert section["real"][0] is False
    assert section["real"][-1] is True
    assert result.plan.data["weirs"]
    assert section["level_m"] == sorted(section["level_m"], reverse=True)   # never rising downstream
    assert result.preview.pixels.shape[1] == 200


def test_navigable_mode_keeps_only_navigable_waterways_real() -> None:
    floor = Field(np.full((101, 101), 540, dtype=np.float32), FRAME)
    no_water = Mask(np.zeros((101, 101), dtype=bool), FRAME)

    def planned(mode: Literal["all", "navigable"]) -> rivers.RiverPlan:
        params = rivers.RiverParams(real_water_until_m=550, min_real_run_game_m=0, floor_smooth_passes=0,
                                    preview_width=200, real_water_rivers=mode)
        return rivers.plan(_canal_and_river(), FRAME, floor, floor, no_water, 540.0, params, CONTEXT).plan

    everything, navigable = planned("all"), planned("navigable")

    brook = "Brook"
    assert all(_section(everything, brook)["real"])
    assert _section(navigable, brook)["real"] == [False] * 6 + [True]       # the junction belongs to the canal
    assert _section(navigable, brook)["navigable"] == [False] * 6 + [True]
    canal = [r for r in navigable.data["rivers"] if r["name"] == "Canal"]
    assert canal
    assert all(all(r["real"]) and all(r["navigable"]) for r in canal)
    assert navigable.data["real_water_rivers"] == "navigable"
    assert [r["real"] for r in everything.data["rivers"] if r["name"] == "Canal"] == [r["real"] for r in canal]


def test_navigability_by_cemt_name_prefix_and_attached_unnamed_canals() -> None:
    def way(i: int, name: str, kind: str, cemt: str, refs: tuple[int, ...]) -> Way:
        return Way(i, name, kind, cemt, tuple((r, 0.0, 0.0) for r in refs))

    ways = OsmWays((way(1, "Ems", "river", "IV", (1, 2)), way(2, "Ems", "river", "", (2, 3)),
                    way(3, "Dortmund-Ems-Kanal", "canal", "", (10, 11)), way(4, "", "canal", "", (11, 12)),
                    way(5, "", "canal", "", (12, 13)), way(6, "", "canal", "", (20, 21)),
                    way(7, "Werse", "river", "", (3, 30)), way(8, "", "river", "", (13, 40))))

    assert rivers._Navigability(("dortmund-ems",)).ways(ways) == {1, 2, 3, 4, 5}
    assert rivers._Navigability(()).ways(ways) == {1, 2}
    assert rivers._Navigability(("",)).ways(ways) == {1, 2}                # an empty entry matches nothing
    recreational = OsmWays((way(1, "Lake Canal", "canal", "0", (1, 2)),))   # CEMT 0: no cargo ships
    assert rivers._Navigability(()).ways(recreational) == set()


def test_water_shape_levels_a_lake_and_makes_a_border() -> None:
    heights = np.tile(np.linspace(560, 600, 101, dtype=np.float32), (101, 1))
    landcover = np.full((51, 51), 30, dtype=np.uint8)              # grassland on the work grid (1/2)
    landcover[20:30, 20:30] = water.WATER_CLASS                    # a pond of 10 x 10 work pixels
    params = water.ShapeParams(real_water_until_m=545, water_sink_m=40)

    shaped = water.shape(Field(heights, FRAME), ClassMap(landcover, FRAME), 540.0, FORMAT, params, CONTEXT)
    layers = water.layers(shaped.core, shaped.border, shaped.real, water.LayerParams(), CONTEXT)

    core = shaped.core.values
    assert core.any()
    assert not shaped.real.values.any()                            # 560+ m: painted, not real water
    assert np.ptp(shaped.heights.values[core]) == 0                 # one lake level
    assert (shaped.landcover.values == params.shore_class).any()    # the border became shore
    assert np.array_equal(layers.water.values, core)
    assert np.array_equal(layers.no_cover.values, core | shaped.border.values)


def test_water_shape_reports_real_lakes_with_the_height_factor() -> None:
    heights = Field(np.full((101, 101), 544, dtype=np.float32), FRAME)
    landcover = np.full((51, 51), 30, dtype=np.uint8)
    landcover[10:40, 10:40] = water.WATER_CLASS
    params = water.ShapeParams(real_water_until_m=560, report_min_ha=0)

    def report(fmt: MapFormat) -> list[str]:
        LOG.clear()
        water.shape(heights, ClassMap(landcover, FRAME), 540.0, fmt, params, CONTEXT)
        return [line for line in LOG if "in game" in line]

    assert "(2 m in game)" in report(FORMAT)[0]
    assert "(4 m in game)" in report(MapFormat(0.4, 0.4, 2, 1))[0]


def test_a_lake_crossed_by_a_real_river_becomes_real_water() -> None:
    # a flat canal at 552 m (WorldCover: a lake), just above the real water limit 550 m; the plan's river along it is
    # real (its valley floor lies lower) - the canal must not stay painted at its own level inside the real river
    heights = Field(np.full((101, 101), 552, dtype=np.float32), FRAME)
    landcover = np.full((51, 51), 30, dtype=np.uint8)
    landcover[23:28, 5:46] = water.WATER_CLASS
    plan = rivers.RiverPlan({"ref_m": 540, "real_water_max_m": 10, "rivers": [{
        "name": "Canal", "points": [[0.0, 0.5], [0.5, 0.5], [1.0, 0.5]], "width_game_m": [10.0] * 3,
        "real": [True] * 3, "level_m": [540.0] * 3, "floor_m": [545.0] * 3}], "weirs": []})
    params = water.ShapeParams(real_water_until_m=550)

    shaped = water.shape(heights, ClassMap(landcover, FRAME), 540.0, FORMAT, params, CONTEXT, plan)

    canal = shaped.core.values[46:54, 15:85]
    assert canal.all()
    assert shaped.real.values[46:54, 15:85].all()
    assert shaped.heights.values[46:54, 15:85].max() < 540


def test_navigable_plan_keeps_water_crossed_by_a_painted_river_painted() -> None:
    # a WorldCover river near the water line, cut by a real canal band and crossed by a painted brook
    heights = Field(np.tile(np.linspace(541, 553, 101, dtype=np.float32)[:, None], (1, 101)), FRAME)
    landcover = np.full((51, 51), 30, dtype=np.uint8)
    landcover[2:49, 21:24] = water.WATER_CLASS

    def shaped_real(mode: str) -> np.ndarray:
        plan = rivers.RiverPlan({"ref_m": 540, "real_water_max_m": 10, "real_water_rivers": mode, "rivers": [
            {"name": "Canal", "points": [[0.0, 0.5], [0.5, 0.5], [1.0, 0.5]], "width_game_m": [20.0] * 3,
             "real": [True] * 3, "navigable": [True] * 3, "level_m": [540.0] * 3, "floor_m": [540.0] * 3},
            {"name": "Brook", "points": [[0.45, 0.0], [0.45, 0.25], [0.45, 0.5]], "width_game_m": [10.0] * 3,
             "real": [False, False, True], "navigable": [False, False, True], "level_m": [545.0, 543.0, 540.0],
             "floor_m": [545.0, 543.0, 540.0]}], "weirs": []})
        params = water.ShapeParams(real_water_until_m=550)
        shaped = water.shape(heights, ClassMap(landcover, FRAME), 540.0, FORMAT, params, CONTEXT, plan)
        assert shaped.real is not None
        return shaped.real.values

    assert shaped_real("all")[10:40, 42:48].any()
    assert not shaped_real("navigable")[10:40, 42:48].any()
    assert shaped_real("navigable")[50, 10:90].all()                    # the canal band stays real


def test_a_brook_into_the_canal_does_not_paint_the_canals_own_water() -> None:
    # the canal's WorldCover water is wider than its real band; the brook's last piece crosses that fringe
    heights = Field(np.tile(np.linspace(541, 553, 101, dtype=np.float32), (101, 1)), FRAME)
    landcover = np.full((51, 51), 30, dtype=np.uint8)
    landcover[19:32, 1:50] = water.WATER_CLASS
    plan = rivers.RiverPlan({"ref_m": 540, "real_water_max_m": 10, "real_water_rivers": "navigable", "rivers": [
        {"name": "Canal", "points": [[0.0, 0.5], [0.5, 0.5], [1.0, 0.5]], "width_game_m": [20.0] * 3,
         "real": [True] * 3, "navigable": [True] * 3, "level_m": [540.0] * 3, "floor_m": [540.0] * 3},
        {"name": "Brook", "points": [[0.45, 0.0], [0.45, 0.25], [0.45, 0.5]], "width_game_m": [10.0] * 3,
         "real": [False, False, True], "navigable": [False, False, True], "level_m": [545.0, 543.0, 540.0],
         "floor_m": [545.0, 543.0, 540.0]}], "weirs": []})

    shaped = water.shape(heights, ClassMap(landcover, FRAME), 540.0, FORMAT, water.ShapeParams(real_water_until_m=550),
                         CONTEXT, plan)

    assert shaped.real.values[39:62, 10:75].all()                      # east of it the water is too high


def test_water_shape_without_water_changes_nothing() -> None:
    heights = Field(np.full((101, 101), 600, dtype=np.float32), FRAME)

    shaped = water.shape(heights, ClassMap(np.full((51, 51), 10), FRAME), 540.0, FORMAT,
                         water.ShapeParams(), CONTEXT)

    assert np.array_equal(shaped.heights.values, heights.values)
    assert not shaped.core.values.any()
    assert shaped.beds.segments == ()


def test_riverbed_and_groove() -> None:
    beds = BedSegments((BedSegment((0.1, 0.5), (0.9, 0.5), 12.0),))
    free = Mask(np.zeros((101, 101), dtype=bool), FRAME)
    heights = Field(np.full((101, 101), 600, dtype=np.float32), FRAME)

    bed = riverbed.paint(beds, free, CONTEXT)
    carved = riverbed.groove(heights, bed.centre, 540.0, MapFormat(0.4, 0.4, 2, 2), riverbed.GrooveParams(), CONTEXT)
    flat = riverbed.groove(heights, free, 540.0, MapFormat(0.4, 0.4, 2, 2), riverbed.GrooveParams(), CONTEXT)

    assert bed.bed.values[50, 50]
    assert int(bed.bed.values[:, 50].sum()) == 3                    # 12 game m = 3 px
    assert carved.values[50, 50] == 600 - 4 * 2                     # 4 game m deep at scale 1:2
    assert carved.values[10, 50] == 600
    assert flat is heights
    real = riverbed.groove(heights, bed.centre, 540.0, MapFormat(0.4, 0.4, 2, 1),
                           riverbed.GrooveParams(), CONTEXT)
    assert real.values[50, 50] == 600 - 4                           # heights 1:1: 4 game m = 4 m real


def test_ramp_lowers_a_riverbed_that_meets_real_water() -> None:
    heights = Field(np.full((101, 101), 600, dtype=np.float32), FRAME)
    real = np.zeros((101, 101), dtype=bool)
    real[:, :20] = True                                             # real water in the west
    bed = np.zeros((101, 101), dtype=bool)
    bed[50, 27:] = True                                             # stops 28 game m before the water
    bed[90, 60:] = True                                             # far from real water
    params = riverbed.RampParams(length_game_m=200)                 # 50 px, corridor 60 game m = 15 px

    ramped = riverbed.ramp(heights, Mask(bed, FRAME), Mask(real, FRAME), 540.0, FORMAT, params, CONTEXT).values

    assert ramped[50, 28] == 540 + 1 / FORMAT.height_game_per_real   # water line + 1 game m at the junction
    assert 542 < ramped[50, 50] < ramped[50, 70] < 600               # linear along the bed
    assert ramped[50, 95] == 600                                    # beyond the length and the corridor
    assert ramped[50, 50] < ramped[55, 50] < 600                     # the corridor follows, softer
    assert ramped[70, 50] == 600                                    # outside the corridor
    assert ramped[90, 80] == 600                                    # never meets real water
    off = riverbed.ramp(heights, Mask(bed, FRAME), Mask(real, FRAME), 540.0, FORMAT, riverbed.RampParams(), CONTEXT)
    assert off is heights


def _valley(bottom_col: int, slope_per_px: float) -> Field:
    """A valley along the map height with its bottom at `bottom_col`, falling 0.1 m per pixel downstream."""
    yy, xx = np.mgrid[0:FRAME.height_px, 0:FRAME.width_px]
    return Field((np.abs(xx - bottom_col) * slope_per_px + (100 - yy) * 0.1).astype(np.float32), FRAME)


def _plan_along_col(col: int, real: list[bool], navigable: bool = False) -> rivers.RiverPlan:
    count = len(real)
    vs = np.linspace(0, 1, count)
    return rivers.RiverPlan({"ref_m": 0, "real_water_max_m": 0, "rivers": [{
        "name": "Sill", "points": [[col / 100, float(v)] for v in vs], "width_game_m": [10.0] * count,
        "real": real, "navigable": [navigable] * count, "level_m": [1.0] * count, "floor_m": [1.0] * count}],
        "weirs": []})


def test_painted_river_moves_into_the_valley_line_and_keeps_its_ends() -> None:
    plan = _plan_along_col(50, [False] * 5)

    moved = river_valley.to_valley(plan, _valley(58, 2.0), river_valley.ValleyParams(), CONTEXT).plan

    points = np.array(moved.data["rivers"][0]["points"]) * 100
    assert points[0].tolist() == [50, 0]
    assert moved.data["rivers"][0]["points"][-1] == [0.5, 1.0]
    middle = points[len(points) // 3: 2 * len(points) // 3]
    assert np.abs(middle[:, 0] - 58).max() <= 1                          # in the valley line
    assert not any(moved.data["rivers"][0]["real"])
    assert len(moved.data["rivers"][0]["width_game_m"]) == len(points)
    navigable = river_valley.to_valley(_plan_along_col(50, [False] * 5, navigable=True), _valley(58, 2.0),
                                       river_valley.ValleyParams(), CONTEXT).plan.data["rivers"][0]
    assert navigable["navigable"] == [True] * len(navigable["points"])
    branch = _plan_along_col(50, [False] * 5)                            # leaves a navigable waterway at its start
    branch.data["rivers"][0]["navigable"][0] = True
    leaving = river_valley.to_valley(branch, _valley(58, 2.0), river_valley.ValleyParams(), CONTEXT).plan
    assert leaving.data["rivers"][0]["navigable"] == [True] + [False] * (len(leaving.data["rivers"][0]["points"]) - 1)


def test_river_stays_where_the_valley_is_flat_and_real_water_is_not_moved() -> None:
    flat = river_valley.to_valley(_plan_along_col(50, [False] * 5), _valley(58, 0.01),
                                  river_valley.ValleyParams(), CONTEXT).plan
    real = _plan_along_col(50, [True] * 5)

    assert np.allclose(np.array(flat.data["rivers"][0]["points"])[:, 0], 0.5)
    assert river_valley.to_valley(real, _valley(58, 2.0), river_valley.ValleyParams(), CONTEXT).plan == real
    off = river_valley.ValleyParams(max_move_real_m=0)
    assert river_valley.to_valley(_plan_along_col(50, [False] * 5), _valley(58, 2.0), off, CONTEXT).plan.data == \
        _plan_along_col(50, [False] * 5).data


def test_real_water_without_a_value_reaches_120_m_above_the_lowest_land() -> None:
    assert rivers.real_water_until(None, 547.0) == 667.0
    assert rivers.real_water_until(600.0, 547.0) == 600.0
