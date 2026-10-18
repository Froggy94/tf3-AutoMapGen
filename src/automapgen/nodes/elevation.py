"""Elevation: read the elevation model (a file or downloaded from OpenTopography), put it on the map grid, find the
height references, encode the heightmap."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal, NamedTuple

import numpy as np
import rasterio
from numpy.typing import NDArray
from PIL import Image as PilImage
from pydantic import BaseModel, ConfigDict
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from scipy.ndimage import distance_transform_edt

from automapgen.graph import NodeContext, NodeParams, data_type, node, param
from automapgen.nodes.images import Image
from automapgen.nodes.maps import MapFormat, MapFrame, Rotation, SourceBounds
from automapgen.nodes.opentopography import DemDownload, OpenTopography
from automapgen.nodes.rasters import Field, resize_floats

_IMPLAUSIBLE_M = 1e5
_DISTORTION_TOLERANCE = 0.02      # x/y resolution may differ by this share
_SCALE_TOLERANCE = 0.05           # horizontal scale may differ from the map format's by this share


@data_type("elevation_source", "Heights of the elevation model in geo orientation (NaN = no data) and its crop.")
class ElevationSource:
    """Heights in metres, rows north -> south (NaN = no data), the crop and the coordinate system of the file."""

    __slots__ = ("bounds", "crs_wkt", "heights")

    def __init__(self, heights: NDArray[np.float32], bounds: SourceBounds, crs_wkt: str) -> None:
        heights.setflags(write=False)
        self.heights = heights
        self.bounds = bounds
        self.crs_wkt = crs_wkt


class GeoBounds(BaseModel):
    """Rectangle in WGS84 degrees."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    west: float
    """Western edge: longitude in degrees (east positive)."""
    south: float
    """Southern edge: latitude in degrees (north positive)."""
    east: float
    """Eastern edge: longitude in degrees."""
    north: float
    """Northern edge: latitude in degrees."""


class ReadParams(NodeParams):
    path: str = ""
    """GeoTIFF of the elevation model (empty: downloaded, see `download`)."""
    crop: GeoBounds | None = None
    """Part of the file to read (None = all)."""
    download: DemDownload | None = None
    """Without a path: download the crop from OpenTopography (once, into the download folder)."""


def elevation_file(params: ReadParams, context: NodeContext) -> Path:
    """The GeoTIFF to read: the given path, else the downloaded crop."""
    if params.path:
        return Path(params.path)
    if params.download is None or params.crop is None:
        raise ValueError("no elevation model: give a path, or a crop and a download")
    c = params.crop
    return OpenTopography(params.download, context.download_folder() / "elevation", context).file(
        c.west, c.south, c.east, c.north)


@node("elevation.read", output="source")
def read(params: ReadParams, context: NodeContext) -> ElevationSource:
    """Reads the elevation model (a GeoTIFF), optionally only a crop given in WGS84 degrees."""
    with rasterio.open(elevation_file(params, context)) as src:
        window = _window(src, params.crop)
        data = src.read(1, window=window, masked=True)
        bounds = _bounds(src, window)
        crs_wkt = src.crs.to_wkt()
    heights = data.astype(np.float32).filled(np.nan)
    heights[np.abs(heights) > _IMPLAUSIBLE_M] = np.nan
    context.log(f"raw data: {heights.shape[1]} x {heights.shape[0]} px")
    return ElevationSource(heights, bounds, crs_wkt)


class ToMapParams(NodeParams):
    rotation_deg: Literal[0, 90, 180, 270] = 0
    """Direction of the map's top edge, clockwise from north."""


class OnMap(NamedTuple):
    heights: Field
    frame: MapFrame


@node("elevation.to_map")
def to_map(source: ElevationSource, map_format: MapFormat, params: ToMapParams, context: NodeContext) -> OnMap:
    """Fills gaps (nearest valid height), turns the raster to the map orientation and resamples it bicubically to the
    heightmap grid."""
    rotation = Rotation(params.rotation_deg)
    heights = rotation.to_game(_filled(source.heights, context))
    frame = MapFrame(map_format.width_px, map_format.height_px, source.bounds, rotation, source.crs_wkt)
    _check_scale(frame, map_format, context)
    heights = resize_floats(heights, frame.width_px, frame.height_px, PilImage.Resampling.BICUBIC)
    return OnMap(Field(heights, frame), frame)


def _filled(heights: NDArray[np.float32], context: NodeContext) -> NDArray[np.float32]:
    invalid = ~np.isfinite(heights)
    if invalid.all():
        raise ValueError("the elevation model contains no valid heights")
    if invalid.any():
        context.log(f"filling {int(invalid.sum())} no-data pixels")
        index = distance_transform_edt(invalid, return_distances=False, return_indices=True)
        heights = heights[tuple(index)]
    context.log(f"height range in file: {float(heights.min()):.1f} m - {float(heights.max()):.1f} m")
    return heights


def _check_scale(frame: MapFrame, map_format: MapFormat, context: NodeContext) -> None:
    width_km, height_km = frame.extent_km
    mpp_x, mpp_y = frame.meters_per_pixel()
    context.log(f"extent about {width_km:.2f} x {height_km:.2f} km, {mpp_x:.2f} x {mpp_y:.2f} m/px")
    if abs(mpp_x - mpp_y) / max(mpp_x, mpp_y) > _DISTORTION_TOLERANCE:
        context.log("warning: aspect ratio of the crop and the heightmap differ - the map will be distorted")
    if abs(frame.horizontal_scale - map_format.game_per_real) / map_format.game_per_real > _SCALE_TOLERANCE:
        context.log(f"warning: crop scale {frame.horizontal_scale:.3f} does not fit the map format "
                    f"{map_format.game_per_real:.3f} - slopes will be steeper/flatter in game")


class ReadOnMapParams(ReadParams):
    rotation_deg: Literal[0, 90, 180, 270] = 0
    """Direction of the map's top edge, clockwise from north."""


@node("elevation.read_on_map", output="heights")
def read_on_map(map_format: MapFormat, params: ReadOnMapParams, context: NodeContext) -> Field:
    """The original elevation model on the heightmap grid, resampled bilinearly while reading (no levelling, gaps
    filled with the nearest height) - what the river planning measures valleys on."""
    rotation = Rotation(params.rotation_deg)
    rows, cols = rotation.geo_shape(map_format.height_px, map_format.width_px)
    with rasterio.open(elevation_file(params, context)) as src:
        window = _window(src, params.crop)
        data = src.read(1, window=window, out_shape=(rows, cols), resampling=Resampling.bilinear, masked=True)
        bounds = _bounds(src, window)
        crs_wkt = src.crs.to_wkt()
    dem = rotation.to_game(data.astype(np.float32).filled(np.nan))
    dem[np.abs(dem) > _IMPLAUSIBLE_M] = np.nan
    if np.isnan(dem).any():
        fill = distance_transform_edt(np.isnan(dem), return_distances=False, return_indices=True)
        dem = dem[tuple(fill)]
    context.log(f"original heights {float(np.nanmin(dem)):.0f}..{float(np.nanmax(dem)):.0f} m")
    return Field(dem, MapFrame(map_format.width_px, map_format.height_px, bounds, rotation, crs_wkt))


def _window(src: Any, crop: GeoBounds | None) -> Any:
    if crop is None:
        return None
    left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, crop.west, crop.south, crop.east, crop.north)
    return from_bounds(left, bottom, right, top, src.transform).round_offsets().round_lengths()


def _bounds(src: Any, window: Any) -> SourceBounds:
    left, bottom, right, top = src.window_bounds(window) if window is not None else src.bounds
    return SourceBounds(left, bottom, right, top, src.crs is not None and src.crs.is_geographic)


class ReferenceParams(NodeParams):
    min_height_m: float | None = param(None, unit="m")
    """Lowest land = game height 0 (water line); None = the lowest point of the map."""
    water_sink_m: float = param(40.0, ge=0, unit="m")
    """Real water core this much below the lowest land (black of the heightmap)."""


class References(NamedTuple):
    land_min_m: float
    black_m: float


@node("elevation.references")
def references(heights: Field, params: ReferenceParams, context: NodeContext) -> References:
    """Height references: lowest land (water line) and black of the heightmap (bottom of real water)."""
    land_min = float(heights.values.min()) if params.min_height_m is None else params.min_height_m
    black = land_min - params.water_sink_m
    context.log(f"land from {land_min:g} m, black = {black:g} m")
    return References(land_min, black)


class EncodeParams(NodeParams):
    bit_depth: Literal[8, 16] = 16
    """Bits per pixel of the heightmap PNG."""
    max_height_m: float | None = param(None, unit="m")
    """White of the heightmap; None = the highest point, rounded up."""


class Heightmap(NamedTuple):
    image: Image
    white_m: float


@node("heightmap.encode")
def encode(heights: Field, black_m: float, params: EncodeParams) -> Heightmap:
    """Real heights -> grey values: black = `black_m`, white = max height; heights outside are clipped."""
    white = float(math.ceil(heights.values.max())) if params.max_height_m is None else params.max_height_m
    if white <= black_m:
        raise ValueError(f"the max height {white} m must be above black {black_m} m")
    normalized = np.clip((heights.values - black_m) / (white - black_m), 0.0, 1.0)
    pixels: NDArray[Any] = (np.round(normalized * 65535.0).astype(np.uint16) if params.bit_depth == 16
                            else np.round(normalized * 255.0).astype(np.uint8))
    return Heightmap(Image(pixels), white)


@node("heightmap.decode", output="heights")
def decode(image: Image, black_m: float, white_m: float, frame: MapFrame) -> Field:
    """Grey values of a 16-bit heightmap -> real heights in m a.s.l., as the game will see them (rounded to the
    heightmap's steps) - for planners that must work on exactly that terrain."""
    pixels = np.asarray(image.pixels, dtype=np.float32)
    return Field(black_m + pixels / 65535 * (white_m - black_m), frame)
