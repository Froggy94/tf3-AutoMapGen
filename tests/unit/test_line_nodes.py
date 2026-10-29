"""Line nodes on small synthetic maps: path and road planning from OSM, terrain under roads and paths, strips."""

from __future__ import annotations

import numpy as np
import pytest
from rasterio.crs import CRS

from automapgen.graph import NodeContext
from automapgen.nodes import lines
from automapgen.nodes.images import Image
from automapgen.nodes.lines import LineCutter, PathPlan, RoadLines, merge_ways, road_profile
from automapgen.nodes.maps import MapFormat, MapFrame, Rotation, SourceBounds
from automapgen.nodes.osm import OsmLine, OsmLines, OsmPoint, OsmPoints
from automapgen.nodes.rasters import Field, Mask

LOG: list[str] = []
CONTEXT = NodeContext("test", LOG.append, lambda f: None, lambda: False)
FRAME = MapFrame(101, 101, SourceBounds(11.0, 47.0, 11.01, 47.0068, True), Rotation.NORTH,
                 CRS.from_epsg(4326).to_wkt())
EMPTY = Mask(np.zeros((101, 101), dtype=bool), FRAME)
NO_PLACES = OsmPoints(())


def _way(way_id: int, tags: dict[str, str], y: float = 0.5, x0: float = 0.05, x1: float = 0.95) -> OsmLines:
    """A west -> east way at map fraction y (node ids way_id*100 + k)."""
    xs = np.linspace(x0, x1, 10)
    points = {way_id * 100 + k: (11.0 + 0.01 * float(x), 47.0068 - 0.0068 * y) for k, x in enumerate(xs)}
    return OsmLines((OsmLine(way_id, tags, tuple(points)),), points)


def test_line_cutter_and_merge() -> None:
    cutter = LineCutter((1000.0, 1000.0), 100.0)

    dense = cutter.densify([(0.0, 0.0), (1.0, 0.0)])
    runs = cutter.runs([(0.0, 0.0), (0.1, 0.0), None, (0.3, 0.0), (0.4, 0.0), (0.5, 0.0)], lambda p: p[0] != 0.4)
    straight = cutter.simplify(np.array([[0.0, 0.0], [1.0, 0.01], [2.0, 0.0]]), 0.1)

    assert len(dense) == 11
    assert runs == [[(0.0, 0.0), (0.1, 0.0)]]
    assert len(straight) == 2
    assert merge_ways([("primary", [1, 2]), ("primary", [2, 3]), ("secondary", [3, 4])]) == [
        ("primary", [1, 2, 3]), ("secondary", [3, 4])]


def test_paths_plan_keeps_paved_tracks_and_cuts_at_water() -> None:
    ways = OsmLines(_way(1, {"highway": "track", "tracktype": "grade1"}).lines
                    + _way(2, {"highway": "track", "tracktype": "grade5"}, y=0.3).lines,
                    {**_way(1, {}).points, **_way(2, {}, y=0.3).points})
    water = np.zeros((101, 101), dtype=bool)
    water[:, 48:53] = True                                         # a river across the map

    plan = lines.plan_paths(ways, NO_PLACES, FRAME, (Mask(water, FRAME),), EMPTY, lines.PathParams(), CONTEXT)

    assert len(plan.data["track"]) == 2                             # the paved track, cut in two at the river
    assert plan.data["trail"] == []


def test_roads_from_osm_skip_bridges_and_towns() -> None:
    road = _way(1, {"highway": "secondary"})
    bridge = _way(2, {"highway": "secondary", "bridge": "yes"}, y=0.3)
    ways = OsmLines(road.lines + bridge.lines, {**road.points, **bridge.points})
    town = OsmPoints((OsmPoint(9, 11.005, 47.0034, {"place": "hamlet"}),))   # 120 m, in the middle of the map

    free = lines.roads_from_osm(ways, NO_PLACES, FRAME, EMPTY, EMPTY, lines.RoadParams(), CONTEXT)
    cut = lines.roads_from_osm(ways, town, FRAME, EMPTY, EMPTY, lines.RoadParams(), CONTEXT)

    assert [r["cls"] for r in free.data["roads"]] == ["secondary"]
    assert len(cut.data["roads"]) == 2                              # left and right of the hamlet


def test_road_profile_is_grade_limited() -> None:
    profile = road_profile(np.array([0.0, 0.0, 10.0, 10.0]), step_m=1.0, smooth_px=0, max_grade=1.0)

    assert np.all(np.abs(np.diff(profile)) <= 1.0 + 1e-9)


def test_terrain_under_roads_and_paths() -> None:
    slope = Field(np.tile(np.linspace(500, 520, 101, dtype=np.float32), (101, 1)).T.copy(), FRAME)   # rises N -> S
    road = RoadLines({"roads": [{"cls": "primary", "pts": [[0.5, 0.1], [0.5, 0.9]]}]})
    path = PathPlan({"track": [[[0.2, 0.1], [0.2, 0.9]]], "trail": []})
    fmt = MapFormat(0.4, 0.4, 2, 2)

    shaped = lines.road_terrain(slope, road, EMPTY, fmt, lines.RoadTerrainParams(), CONTEXT)
    path_shaped = lines.path_terrain(shaped.heights, path, EMPTY, shaped.core, fmt, lines.PathTerrainParams(), CONTEXT)
    nothing = lines.road_terrain(slope, RoadLines({"roads": []}), EMPTY, fmt, lines.RoadTerrainParams(), CONTEXT)

    assert shaped.core.values[50, 50]
    assert not np.array_equal(shaped.heights.values[:, 50], slope.values[:, 50])
    assert np.array_equal(shaped.heights.values[:, 5], slope.values[:, 5])          # far from the road unchanged
    assert path_shaped.values[50, 20] < shaped.heights.values[50, 20]                # the path is slightly lowered
    assert nothing.heights is slope


def test_road_terrain_sinks_by_the_height_factor() -> None:
    flat = Field(np.full((101, 101), 500, dtype=np.float32), FRAME)
    road = RoadLines({"roads": [{"cls": "primary", "pts": [[0.5, 0.1], [0.5, 0.9]]}]})
    params = lines.RoadTerrainParams(sink_game_m=1.0)

    scaled = lines.road_terrain(flat, road, EMPTY, MapFormat(0.4, 0.4, 2, 2), params, CONTEXT)
    real = lines.road_terrain(flat, road, EMPTY, MapFormat(0.4, 0.4, 2, 1), params, CONTEXT)

    assert scaled.heights.values[50, 50] == pytest.approx(498)        # 1 game m = 2 real m at 1:2
    assert real.heights.values[50, 50] == pytest.approx(499)          # 1 game m = 1 real m with real heights


def test_strip_and_preview_base() -> None:
    path = PathPlan({"track": [[[0.1, 0.5], [0.9, 0.5]]], "trail": []})
    biome = Image(np.zeros((26, 26, 3), dtype=np.uint8))
    water = Mask(np.zeros((101, 101), dtype=bool), FRAME)
    heightmap = Image(np.ones((101, 101), dtype=np.uint16))

    strip = lines.strip(path, EMPTY, lines.GapParams(width_game_m=8))
    none = lines.strip(path, EMPTY, lines.GapParams(width_game_m=0))
    base = lines.preview_base(biome, water, heightmap, lines.BaseParams(width=200))

    assert strip.values[50, 50]
    assert not none.values.any()
    assert base.pixels.shape == (200, 200, 3)
