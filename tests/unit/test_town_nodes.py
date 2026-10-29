"""Town nodes on small synthetic maps: town planning, settling, heightmap decoding."""

from __future__ import annotations

import numpy as np
from rasterio.crs import CRS

from automapgen.graph import NodeContext
from automapgen.nodes import elevation, towns
from automapgen.nodes.images import Image
from automapgen.nodes.maps import MapFormat, MapFrame, Rotation, SourceBounds
from automapgen.nodes.osm import OsmPoint, OsmPoints
from automapgen.nodes.rasters import Field, Mask
from automapgen.nodes.towns import TownList

LOG: list[str] = []
CONTEXT = NodeContext("test", LOG.append, lambda f: None, lambda: False)
# 201 x 201 px = 800 x 800 game m at scale 1:1 over a small square near 47° N
FRAME = MapFrame(201, 201, SourceBounds(11.0, 47.0, 11.0106, 47.0072, True), Rotation.NORTH,
                 CRS.from_epsg(4326).to_wkt())
FORMAT = MapFormat(0.8, 0.8, 1, 1)
EMPTY = Mask(np.zeros((201, 201), dtype=bool), FRAME)
FLAT = Field(np.full((201, 201), 600, dtype=np.float32), FRAME)


def _place(name: str, u: float, v: float, population: int, kind: str = "village") -> OsmPoint:
    return OsmPoint(hash(name) % 1000, 11.0 + 0.0106 * u, 47.0072 - 0.0072 * v,
                    {"place": kind, "name": name, "population": str(population)})


def _plan(places: OsmPoints, water: Mask = EMPTY, **params: object) -> towns.Towns:
    return towns.plan(FLAT, Image(np.full((201, 201), 1000, dtype=np.uint16)), 500.0, water, EMPTY, EMPTY, EMPTY,
                      places, Image(np.zeros((51, 51, 3), dtype=np.uint8)), FORMAT,
                      towns.TownParams(**params), CONTEXT)


def test_towns_largest_first_at_a_distance() -> None:
    places = OsmPoints((_place("Small", 0.2, 0.2, 400), _place("Big", 0.5, 0.5, 9000),
                        _place("Near", 0.55, 0.5, 5000), _place("Tiny", 0.8, 0.8, 100)))

    result = _plan(places, max_towns=5, min_distance_game_m=300)

    assert [t["name"] for t in result.towns.data] == ["Big", "Small"]      # Near too close, Tiny too small
    assert result.towns.data[0]["capacity"] > result.towns.data[1]["capacity"]
    assert 'name = "Big"' in result.lua.text
    assert result.lua.text.startswith("function data()")


def test_ignore_size_starts_all_at_the_minimum_and_keeps_the_choice() -> None:
    places = OsmPoints((_place("Small", 0.2, 0.2, 400), _place("Big", 0.5, 0.5, 90000),
                        _place("Near", 0.55, 0.5, 5000), _place("Third", 0.8, 0.8, 300)))

    normal = _plan(places, max_towns=2, min_distance_game_m=300)
    equal = _plan(places, max_towns=2, min_distance_game_m=300, ignore_size=True, min_start_capacity=45)

    assert [t["name"] for t in equal.towns.data] == [t["name"] for t in normal.towns.data] == ["Big", "Small"]
    assert [t["capacity"] for t in equal.towns.data] == [45, 45]


def test_towns_avoid_water() -> None:
    water = np.zeros((201, 201), dtype=bool)
    water[80:120, 80:120] = True

    result = _plan(OsmPoints((_place("Island", 0.5, 0.5, 9000),)), Mask(water, FRAME), max_towns=1,
                   snap_radius_game_m=0)

    assert result.towns.data == []


def test_max_towns_from_the_map_area() -> None:
    places = OsmPoints(tuple(_place(f"P{i}", 0.1 + 0.2 * i, 0.5, 1000 + i) for i in range(4)))

    assert _plan(places, max_towns=0, towns_per_100_km2=300, min_distance_game_m=10).towns.data.__len__() == 2


def test_settle_evens_out_around_a_town() -> None:
    bumpy = Field(600 + np.random.default_rng(1).normal(0, 5, (201, 201)).astype(np.float32), FRAME)
    town = TownList([{"name": "T", "x": 0.0, "y": 0.0, "capacity": 100}])

    settled = towns.settle(bumpy, EMPTY, 500.0, FORMAT, towns.SettleParams(), CONTEXT, town)
    nothing = towns.settle(bumpy, EMPTY, 500.0, FORMAT, towns.SettleParams(), CONTEXT)

    centre = (slice(90, 111), slice(90, 111))
    assert settled.heights.values[centre].std() < bumpy.values[centre].std() / 5
    assert np.array_equal(nothing.heights.values, bumpy.values)


def test_heightmap_decode_inverts_encode() -> None:
    heights = Field(np.linspace(500, 900, 201 * 201, dtype=np.float32).reshape(201, 201), FRAME)

    encoded = elevation.encode(heights, 500.0, elevation.EncodeParams())
    decoded = elevation.decode(encoded.image, 500.0, encoded.white_m, FRAME)

    assert np.abs(decoded.values - heights.values).max() < 0.01
