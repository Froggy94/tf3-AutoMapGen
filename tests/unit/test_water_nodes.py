"""Water nodes on small synthetic maps: river plan, water shaping, water layers, riverbed, groove, helpers."""

from __future__ import annotations

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


def test_water_shape_levels_a_lake_and_makes_a_border() -> None:
    heights = np.tile(np.linspace(560, 600, 101, dtype=np.float32), (101, 1))
    landcover = np.full((51, 51), 30, dtype=np.uint8)              # grassland on the work grid (1/2)
    landcover[20:30, 20:30] = water.WATER_CLASS                    # a pond of 10 x 10 work pixels
    params = water.ShapeParams(real_water_until_m=545, water_sink_m=40)

    shaped = water.shape(Field(heights, FRAME), ClassMap(landcover, FRAME), 540.0, params, CONTEXT)
    layers = water.layers(shaped.core, shaped.border, shaped.real, water.LayerParams(), CONTEXT)

    core = shaped.core.values
    assert core.any()
    assert not shaped.real.values.any()                            # 560+ m: painted, not real water
    assert np.ptp(shaped.heights.values[core]) == 0                 # one lake level
    assert (shaped.landcover.values == params.shore_class).any()    # the border became shore
    assert np.array_equal(layers.water.values, core)
    assert np.array_equal(layers.no_cover.values, core | shaped.border.values)


def test_water_shape_without_water_changes_nothing() -> None:
    heights = Field(np.full((101, 101), 600, dtype=np.float32), FRAME)

    shaped = water.shape(heights, ClassMap(np.full((51, 51), 10), FRAME), 540.0, water.ShapeParams(), CONTEXT)

    assert np.array_equal(shaped.heights.values, heights.values)
    assert not shaped.core.values.any()
    assert shaped.beds.segments == ()


def test_riverbed_and_groove() -> None:
    beds = BedSegments((BedSegment((0.1, 0.5), (0.9, 0.5), 12.0),))
    free = Mask(np.zeros((101, 101), dtype=bool), FRAME)
    heights = Field(np.full((101, 101), 600, dtype=np.float32), FRAME)

    bed = riverbed.paint(beds, free, CONTEXT)
    carved = riverbed.groove(heights, bed.centre, 540.0, MapFormat(0.4, 0.4, 2), riverbed.GrooveParams(), CONTEXT)
    flat = riverbed.groove(heights, free, 540.0, MapFormat(0.4, 0.4, 2), riverbed.GrooveParams(), CONTEXT)

    assert bed.bed.values[50, 50]
    assert int(bed.bed.values[:, 50].sum()) == 3                    # 12 game m = 3 px
    assert carved.values[50, 50] == 600 - 4 * 2                     # 4 game m deep at scale 1:2
    assert carved.values[10, 50] == 600
    assert flat is heights


def _valley(bottom_col: int, slope_per_px: float) -> Field:
    """A valley along the map height with its bottom at `bottom_col`, falling 0.1 m per pixel downstream."""
    yy, xx = np.mgrid[0:FRAME.height_px, 0:FRAME.width_px]
    return Field((np.abs(xx - bottom_col) * slope_per_px + (100 - yy) * 0.1).astype(np.float32), FRAME)


def _plan_along_col(col: int, real: list[bool]) -> rivers.RiverPlan:
    count = len(real)
    vs = np.linspace(0, 1, count)
    return rivers.RiverPlan({"ref_m": 0, "real_water_max_m": 0, "rivers": [{
        "name": "Sill", "points": [[col / 100, float(v)] for v in vs], "width_game_m": [10.0] * count,
        "real": real, "level_m": [1.0] * count, "floor_m": [1.0] * count}], "weirs": []})


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
