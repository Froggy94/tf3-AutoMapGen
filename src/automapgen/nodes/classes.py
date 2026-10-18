"""Class map nodes: classes from fields, majority filter, selection, and the landcover source."""

from __future__ import annotations

import math

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject, transform_bounds
from scipy.ndimage import gaussian_filter

from automapgen.graph import NodeContext, NodeParams, node, param
from automapgen.nodes.maps import MapFrame
from automapgen.nodes.rasters import ClassMap, Field, Mask, resize_nearest

_WORLDCOVER_TILE_DEG = 3


class BandParams(NodeParams):
    limits: list[float]
    """Borders between the classes (ascending)."""
    first: int = param(1, ge=0, le=255)
    """Code of the lowest class; the next band gets first + 1 and so on."""


@node("classes.bands", output="classes")
def bands(field: Field, params: BandParams) -> ClassMap:
    """Classes by value bands, e.g. biomes by effective height."""
    if params.first + len(params.limits) > 255:
        raise ValueError("too many bands for class codes 0..255")
    return ClassMap(params.first + np.digitize(field.values, params.limits), field.frame)


class MajorityParams(NodeParams):
    codes: list[int]
    """The classes that compete; every pixel gets one of them."""
    radius_m: float = param(80.0, gt=0, unit="m")
    """Gaussian radius (real metres) of the vote."""


@node("classes.majority", output="classes")
def majority(classes: ClassMap, params: MajorityParams) -> ClassMap:
    """Each pixel takes the class most frequent around it: smooth, natural borders without specks."""
    sigma = params.radius_m / classes.pixel_m
    votes = np.stack([gaussian_filter((classes.values == c).astype(np.float32), sigma=sigma) for c in params.codes])
    return classes.like(np.asarray(params.codes)[np.argmax(votes, axis=0)])


class SelectParams(NodeParams):
    codes: list[int]
    """Class codes that form the mask."""


@node("classes.select", output="mask")
def select(classes: ClassMap, params: SelectParams) -> Mask:
    """Pixels whose class is one of `codes`."""
    return Mask(np.isin(classes.values, params.codes), classes.frame)


@node("classes.to_map", output="classes")
def to_map(classes: ClassMap) -> ClassMap:
    """The class map on the heightmap grid (nearest neighbour)."""
    return classes.like(resize_nearest(classes.values, classes.frame.width_px, classes.frame.height_px))


class WorldCoverParams(NodeParams):
    url: str = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
                "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif")
    """Address of the ESA WorldCover tiles ({tile} = tile name like N45E009)."""
    work_scale: int = param(2, ge=1)
    """Result has 1/work_scale of the heightmap resolution."""


@node("landcover.worldcover", output="landcover")
def worldcover(frame: MapFrame, params: WorldCoverParams, context: NodeContext) -> ClassMap:
    """ESA WorldCover 10 m classes for the map (most frequent class per pixel), read tile by tile over the network.
    Data: ESA WorldCover project 2021, CC BY 4.0."""
    cols, rows = frame.work_grid(params.work_scale)
    geo_rows, geo_cols = frame.rotation.geo_shape(rows, cols)
    b = frame.bounds
    crs = CRS.from_wkt(frame.crs_wkt)
    destination = from_bounds(b.left, b.bottom, b.right, b.top, geo_cols, geo_rows)
    result = np.zeros((geo_rows, geo_cols), dtype=np.uint8)
    lon0, lat0, lon1, lat1 = transform_bounds(crs, "EPSG:4326", b.left, b.bottom, b.right, b.top)
    step = _WORLDCOVER_TILE_DEG
    for tile_lat in range(math.floor(lat0 / step) * step, math.ceil(lat1 / step) * step, step):
        for tile_lon in range(math.floor(lon0 / step) * step, math.ceil(lon1 / step) * step, step):
            tile = (f"{'N' if tile_lat >= 0 else 'S'}{abs(tile_lat):02d}"
                    f"{'E' if tile_lon >= 0 else 'W'}{abs(tile_lon):03d}")
            context.log(f"tile {tile}")
            context.check_cancelled()
            part = np.zeros_like(result)
            with rasterio.open("/vsicurl/" + params.url.format(tile=tile)) as src:
                reproject(source=rasterio.band(src, 1), destination=part, dst_transform=destination, dst_crs=crs,
                          src_nodata=0, dst_nodata=0, resampling=Resampling.mode)
            result[part > 0] = part[part > 0]
    if not (result > 0).any():
        raise ValueError("no WorldCover data found for this crop")
    return ClassMap(frame.rotation.to_game(result), frame)
