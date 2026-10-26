"""Arbitrary map bearings: synthetic georeferenced data only, no network or game files."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from pydantic import ValidationError
from rasterio.crs import CRS
from rasterio.transform import Affine, from_bounds
from rasterio.warp import transform

from automapgen.graph import NodeContext
from automapgen.graph.cache import LineageKeys
from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.compiler import GraphCompiler
from automapgen.graph.library import FolderLibrary
from automapgen.graph.paramsets import ParameterSet
from automapgen.graphs import GRAPHS_DIR
from automapgen.nodes import BUILTIN_MODULES, classes, elevation, farmland, lines, towns
from automapgen.nodes.climate.preset import GradientZone
from automapgen.nodes.climate.zones import ZoneMask
from automapgen.nodes.maps import GeoCenter, MapFormat, MapFrame, MapProjection, Rotation, SourceBounds
from automapgen.nodes.osm import Area, OsmLine, OsmLines, OsmPoint, OsmPoints, OsmWays, Way
from automapgen.nodes.rasters import Field, Mask, RasterCodec
from automapgen.nodes.rivers import RiverNetwork

# Affine 3 prefers @, but * also supports the Affine 2.x versions used by Rasterio installations.
pytestmark = pytest.mark.filterwarnings("ignore:Use .*matmul.*:PendingDeprecationWarning")

CENTER = GeoCenter(lon=11.4, lat=47.3)
FORMAT = MapFormat(0.24, 0.48, 2)
CONTEXT = NodeContext("rotation", lambda m: None, lambda f: None, lambda: False)


@pytest.mark.parametrize("angle", [0, 45, 17.3, 90, 180, 270, 315, 359.9])
def test_metric_frame_round_trip_scale_and_bearing(angle: float) -> None:
    frame = MapFrame.centered(FORMAT, CENTER, angle)
    projection = MapProjection(frame)
    uv = [(0, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5), (0.2, 0.8), (-0.2, 1.1)]
    assert np.allclose(projection.ring_to_uv(projection.uv_to_wgs84(uv)), uv, atol=1e-9)
    assert projection.uv_to_wgs84([(0.5, 0.5)])[0] == pytest.approx((CENTER.lon, CENTER.lat))
    assert frame.extent_km == pytest.approx((0.48, 0.96))
    assert frame.meters_per_pixel() == pytest.approx((8, 8))
    assert frame.horizontal_scale == pytest.approx(0.5)
    assert MapFrame.from_json(json.loads(json.dumps(frame.to_json()))) == frame
    t = frame.uv_transform
    assert t is not None
    x, y = t * (0.5, 0)
    assert math.degrees(math.atan2(x, y)) % 360 == pytest.approx(angle)
    for w, h in ((frame.width_px, frame.height_px), (13, 21)):
        raster = frame.raster_transform(w, h)
        assert raster * (0.5, 0.5) == pytest.approx(t * (0, 0))
        assert raster * (w - 0.5, h - 0.5) == pytest.approx(t * (1, 1))
    assert frame.raster_transform(1, 1) * (0.5, 0.5) == pytest.approx((0, 0))
    west, south, east, north = projection.wgs84_box()
    for lon, lat in projection.footprint(512):
        assert west - 1e-7 <= lon <= east + 1e-7
        assert south - 1e-7 <= lat <= north + 1e-7


def test_diagonal_map_top_is_northeast_and_envelope_is_not_map_extent() -> None:
    frame = MapFrame.centered(FORMAT, CENTER, 45)
    lon, lat = MapProjection(frame).uv_to_wgs84([(0.5, 0)])[0]
    assert lon > CENTER.lon
    assert lat > CENTER.lat
    assert frame.bounds.extent_km() == pytest.approx((1.44 / math.sqrt(2),) * 2)
    assert frame.extent_km == pytest.approx((0.48, 0.96))
    with pytest.raises(ValueError, match="antimeridian"):
        MapFrame.centered(MapFormat(10, 10, 2), GeoCenter(lon=179.999, lat=10), 45)


@pytest.mark.parametrize("angle", [-1, 360, float("nan"), float("inf")])
def test_invalid_bearings_rejected(angle: float) -> None:
    for model in (elevation.ReadParams, elevation.ToMapParams, elevation.ReadOnMapParams):
        with pytest.raises(ValidationError):
            model(rotation_deg=angle)


def test_crop_centre_fallback_and_explicit_centre() -> None:
    crop = elevation.GeoBounds(west=11.3, east=11.5, south=47.2, north=47.4)
    expected = MapFrame.centered(FORMAT, CENTER, 45)
    assert elevation.selection_frame(elevation.ReadParams(crop=crop, rotation_deg=45), FORMAT) == expected
    assert elevation.selection_frame(elevation.ReadParams(center=CENTER, rotation_deg=45), FORMAT) == expected
    assert elevation.selection_frame(elevation.ReadParams(crop=crop, rotation_deg=90), FORMAT) is None
    with pytest.raises(ValueError, match="requires center"):
        elevation.selection_frame(elevation.ReadParams(rotation_deg=45), FORMAT)
    with pytest.raises(ValueError, match="requires map_format"):
        elevation.selection_frame(elevation.ReadParams(center=CENTER, rotation_deg=45), None)


def _dem(path: Path, bounds: tuple[float, float, float, float] = (-1200, -1200, 1200, 1200),
         rotated_source: bool = False, holes: bool = False) -> Path:
    frame = MapFrame.centered(FORMAT, CENTER, 45)
    w = h = 600
    t = from_bounds(*bounds, w, h)
    if rotated_source:
        t = Affine.rotation(12) * t
    yy, xx = np.mgrid[:h, :w] + 0.5
    x, y = t * (xx, yy)
    values = (500 + 0.1 * x + 0.2 * y).astype(np.float32)
    if holes:
        values[295:305, 295:305] = -9999
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1, dtype="float32", nodata=-9999,
                       crs=frame.crs_wkt, transform=t) as dst:
        dst.write(values, 1)
    return path


@pytest.mark.parametrize("angle", [45, 17.3, 315])
@pytest.mark.parametrize("rotated_source", [False, True])
def test_both_elevation_paths_match_projected_plane(tmp_path: Path, angle: float, rotated_source: bool) -> None:
    path = _dem(tmp_path / "plane.tif", rotated_source=rotated_source)
    params = elevation.ReadOnMapParams(path=str(path), center=CENTER, rotation_deg=angle)
    source = elevation.read(params, CONTEXT, FORMAT)
    result = elevation.to_map(source, FORMAT, elevation.ToMapParams(rotation_deg=angle), CONTEXT)
    original = elevation.read_on_map(FORMAT, params, CONTEXT)
    assert result.frame == original.frame == source.frame
    assert result.frame.uv_transform is not None
    vv, uu = np.mgrid[:FORMAT.height_px, :FORMAT.width_px].astype(float)
    x, y = result.frame.uv_transform * (uu / (FORMAT.width_px - 1), vv / (FORMAT.height_px - 1))
    expected = 500 + 0.1 * x + 0.2 * y
    assert np.allclose(result.heights.values, expected, atol=0.05)
    assert np.allclose(original.values, expected, atol=0.05)
    assert np.isfinite(result.heights.values).all()
    # Same geographic landmark lands on the same pixel used to sample the DEM.
    uv = (0.25, 0.75)
    ll = MapProjection(result.frame).uv_to_wgs84([uv])
    assert MapProjection(result.frame).ring_to_uv(ll)[0] == pytest.approx(uv)
    filename = tmp_path / "grid.npz"
    codec = RasterCodec(lambda v, f: Field(v, f))
    codec.save(result.heights, filename)
    assert codec.load(filename).frame == result.frame
    with pytest.raises(ValueError, match="same rotation"):
        elevation.to_map(source, FORMAT, elevation.ToMapParams(rotation_deg=0), CONTEXT)
    with pytest.raises(ValueError, match="same map_format"):
        elevation.to_map(source, MapFormat(0.24, 0.48, 3), elevation.ToMapParams(rotation_deg=angle), CONTEXT)


def test_geographic_dem_is_reprojected_not_rotated_in_degrees(tmp_path: Path) -> None:
    frame = MapFrame.centered(FORMAT, CENTER, 32.5)
    path = tmp_path / "geographic.tif"
    t = from_bounds(11.38, 47.28, 11.42, 47.32, 600, 600)
    yy, xx = np.mgrid[:600, :600] + 0.5
    lons, lats = t * (xx, yy)
    xs, ys = transform("EPSG:4326", frame.crs_wkt, lons.ravel(), lats.ravel())
    plane = (500 + 0.1 * np.array(xs) + 0.2 * np.array(ys)).reshape(600, 600).astype(np.float32)
    with rasterio.open(path, "w", driver="GTiff", width=600, height=600, count=1, dtype="float32",
                       crs="EPSG:4326", transform=t) as dst:
        dst.write(plane, 1)
    params = elevation.ReadParams(path=str(path), center=CENTER, rotation_deg=32.5)
    source = elevation.read(params, CONTEXT, FORMAT)
    result = elevation.to_map(source, FORMAT, elevation.ToMapParams(rotation_deg=32.5), CONTEXT)
    vv, uu = np.mgrid[:frame.height_px, :frame.width_px].astype(float)
    assert frame.uv_transform is not None
    x, y = frame.uv_transform * (uu / (frame.width_px - 1), vv / (frame.height_px - 1))
    assert np.allclose(result.heights.values, 500 + 0.1 * x + 0.2 * y, atol=0.1)


def test_missing_corners_rejected_but_internal_holes_filled(tmp_path: Path) -> None:
    path = _dem(tmp_path / "small.tif", (-250, -500, 250, 500))
    params = elevation.ReadOnMapParams(path=str(path), center=CENTER, rotation_deg=45)
    for reader in (lambda: elevation.read(params, CONTEXT, FORMAT),
                   lambda: elevation.read_on_map(FORMAT, params, CONTEXT)):
        with pytest.raises(ValueError, match="does not cover the rotated map footprint"):
            reader()
    path = _dem(tmp_path / "holes.tif", holes=True)
    field = elevation.read_on_map(FORMAT, params.model_copy(update={"path": str(path)}), CONTEXT)
    assert np.isfinite(field.values).all()
    # A rectangular file can still have missing coverage encoded as a no-data region at its edge.
    with rasterio.open(path, "r+") as dst:
        values = dst.read(1)
        values[:300, :] = -9999
        dst.write(values, 1)
    with pytest.raises(ValueError, match="edge no-data") as error:
        elevation.read_on_map(FORMAT, params.model_copy(update={"path": str(path)}), CONTEXT)
    assert "try COP30" in str(error.value)
    assert "increasing the download margin alone may not help" in str(error.value)


@pytest.mark.parametrize("rotation", list(Rotation))
def test_legacy_cardinal_results_unchanged(tmp_path: Path, rotation: Rotation) -> None:
    path = _dem(tmp_path / "old.tif")
    source = elevation.read(elevation.ReadParams(path=str(path)), CONTEXT)
    fmt = MapFormat(2.396, 2.396, 1)  # 600 samples: no resizing
    result = elevation.to_map(source, fmt, elevation.ToMapParams(rotation_deg=rotation.value), CONTEXT)
    params = elevation.ReadOnMapParams(path=str(path), rotation_deg=rotation.value)
    original = elevation.read_on_map(fmt, params, CONTEXT)
    assert result.frame.uv_transform is None
    assert np.array_equal(result.heights.values, np.rot90(source.heights, rotation.value // 90))
    assert np.array_equal(original.values, result.heights.values)
    assert result.frame == original.frame
    assert "uv_transform" not in result.frame.to_json()
    assert MapFrame.from_json(result.frame.to_json()) == result.frame


@pytest.mark.parametrize("angle", [45, 17.3])
def test_worldcover_and_climate_share_the_grid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, angle: float) -> None:
    frame = MapFrame.centered(FORMAT, CENTER, angle)
    # A north/south categorical split in local projected metres, away from a WorldCover tile boundary.
    path = tmp_path / "cover.tif"
    codes = np.where(np.arange(600)[:, None] < 300, 10, 80).repeat(600, axis=1).astype(np.uint8)
    with rasterio.open(path, "w", driver="GTiff", width=600, height=600, count=1, dtype="uint8",
                       crs=frame.crs_wkt, transform=from_bounds(-1200, -1200, 1200, 1200, 600, 600)) as dst:
        dst.write(codes, 1)
    real_open = rasterio.open
    seen = []

    def open_tile(name: str, *args: Any, **kwargs: Any) -> Any:
        seen.append(name)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(rasterio, "open", open_tile)
    landcover = classes.worldcover(frame, classes.WorldCoverParams(work_scale=1), CONTEXT)
    assert seen
    assert all("ESA_WorldCover" in name for name in seen)
    assert set(np.unique(landcover.values)) == {10, 80}
    vv, uu = np.mgrid[:frame.height_px, :frame.width_px].astype(float)
    assert frame.uv_transform is not None
    _, ys = frame.uv_transform * (uu / (frame.width_px - 1), vv / (frame.height_px - 1))
    assert (landcover.values[ys > 20] == 10).all()
    assert (landcover.values[ys < -20] == 80).all()
    for scale in (2, 1000):
        coarse = classes.worldcover(frame, classes.WorldCoverParams(work_scale=scale), CONTEXT)
        assert coarse.frame == frame
        assert (coarse.width, coarse.height) == frame.work_grid(scale)
        assert set(np.unique(coarse.values)) <= {10, 80}
    zone = GradientZone(start_lat=CENTER.lat, end_lat=CENTER.lat - 1)
    mask = ZoneMask(frame).beyond_start(zone)
    assert mask[ys < -20].all()
    assert not mask[ys > 20].any()
    assert mask[0, 0] != mask[0, -1] or mask[-1, 0] != mask[-1, -1] or (mask[:, 0] != mask[:, -1]).any()


def test_vectors_are_positioned_and_clipped_in_map_space() -> None:
    frame = MapFrame.centered(FORMAT, CENTER, 45)
    projection = MapProjection(frame)
    uv = [(-0.5, 0.5), (0.25, 0.5), (0.75, 0.5), (1.5, 0.5), (-0.2, -0.2), (-0.1, -0.1)]
    coords = projection.uv_to_wgs84(uv)
    pts = dict(enumerate(coords))
    roads = OsmLines((OsmLine(1, {"highway": "secondary"}, (0, 1, 2, 3)),
                      OsmLine(2, {"highway": "secondary"}, (4, 5))), pts)
    empty = Mask(np.zeros((frame.height_px, frame.width_px), dtype=bool), frame)
    result = lines.roads_from_osm(roads, OsmPoints(()), frame, empty, empty,
                                  lines.RoadParams(town_radius_m={}, min_length_m=0), CONTEXT)
    assert len(result.data["roads"]) == 1
    line = np.asarray(result.data["roads"][0]["pts"])
    assert ((line >= 0) & (line <= 1)).all()
    assert np.allclose(line[:, 1], 0.5)
    ways = OsmWays((Way(1, "River", "river", "", tuple((i, *p) for i, p in pts.items())),))
    network = RiverNetwork.from_ways(ways, projection, frame.width_px, frame.height_px)
    assert set(network.pos) == {1, 2}
    assert network.pos[1] == pytest.approx((0.25 * (frame.width_px - 1), 0.5 * (frame.height_px - 1)))


def test_towns_and_fields_exclude_the_envelope_outside_the_footprint() -> None:
    frame = MapFrame.centered(FORMAT, CENTER, 45)
    projection = MapProjection(frame)
    # An envelope corner is outside the rotated rectangle, despite being in the data query's bounding box.
    west, south, _, _ = projection.wgs84_box()
    center_lon, center_lat = projection.uv_to_wgs84([(0.5, 0.5)])[0]
    tags = {"place": "town", "population": "9000"}
    places = OsmPoints((OsmPoint(1, center_lon, center_lat, {**tags, "name": "Inside"}),
                        OsmPoint(2, west, south, {**tags, "name": "Outside"})))
    found = towns._places(places, towns.TownParams(), frame, CONTEXT)
    assert [place.name for place in found] == ["Inside"]
    assert (found[0].u, found[0].v) == pytest.approx((0.5, 0.5))
    inside = projection.uv_to_wgs84([(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)])
    outside = projection.uv_to_wgs84([(-0.4, 0.4), (-0.2, 0.4), (-0.2, 0.6), (-0.4, 0.6)])
    fields = [Area({}, (tuple(inside),), ()), Area({}, (tuple(outside),), ())]
    labels = farmland._rasterise(fields, projection, frame.width_px, frame.height_px)
    assert labels[frame.height_px // 2, frame.width_px // 2] == 1
    assert not (labels == 2).any()


def test_all_map_graphs_accept_center_and_non_cardinal_rotation() -> None:
    library = FolderLibrary([GRAPHS_DIR])
    compiler = GraphCompiler(NodeCatalog.discover(BUILTIN_MODULES), library)
    configs = Path(__file__).resolve().parents[2] / "configs"
    preset = ParameterSet.load(configs / "presets" / "alpine_realistic.toml")
    for graph_id in ("map", "map_roads", "map_parcels"):
        doc = library.get(graph_id)
        assert doc is not None
        params = ParameterSet.layered([preset], graph_id)
        params = ParameterSet(graph_id, {"map_name": "Rotated", "center": CENTER.model_dump(), "rotation_deg": 45},
                              defaults=params.defaults)
        plan = compiler.compile(doc, params)
        readers = [n for n in plan.nodes if n.spec.type_id in ("elevation.read", "elevation.read_on_map")]
        assert len(readers) == 2
        for reader in readers:
            assert reader.params.model_dump()["rotation_deg"] == 45
            assert reader.params.model_dump()["center"] == CENTER.model_dump()
            assert reader.inputs["map_format"]
        other = compiler.compile(doc, ParameterSet(graph_id, {**params.params, "rotation_deg": 32.5},
                                                     defaults=params.defaults))
        keys, changed = LineageKeys.of(plan, {}), LineageKeys.of(other, {})
        for n in plan.nodes:
            if n.spec.type_id in ("elevation.to_map", "elevation.read_on_map", "landcover.worldcover"):
                refs = [ref for ref in keys if ref.node == n.id]
                assert refs
                assert all(keys[ref] != changed[ref] for ref in refs)


def test_old_frame_json_still_loads() -> None:
    old = MapFrame(5, 7, SourceBounds(11, 47, 12, 48, True), Rotation.WEST, CRS.from_epsg(4326).to_wkt())
    assert MapFrame.from_json(old.to_json()) == old
