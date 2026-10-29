"""Where a map lies: the game map format, the real crop and the heightmap grid over it."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from rasterio.crs import CRS
from rasterio.warp import transform, transform_bounds

from automapgen.graph import NodeContext, NodeParams, data_type, node, param

GAME_METERS_PER_PIXEL = 4
"""TF3 fact: one heightmap pixel covers 4 game metres."""

_KM_PER_DEGREE_LON_AT_EQUATOR = 111.32
_KM_PER_DEGREE_LAT = 110.57


class JsonDataCodec:
    """Codec for small data classes: `value.to_json()` is written, `load(data)` makes the value again."""

    suffix = ".json"

    def __init__(self, load: Callable[[Any], Any]) -> None:
        self._load = load

    def save(self, value: Any, path: Path) -> None:
        path.write_text(json.dumps(value.to_json()), encoding="utf-8")

    def load(self, path: Path) -> Any:
        return self._load(json.loads(path.read_text(encoding="utf-8")))


@data_type("map_format", "Size and scale of the game map.", preview=str,
           codec=JsonDataCodec(lambda data: MapFormat.from_json(data)))
@dataclass(frozen=True)
class MapFormat:
    """Size of the game map in km, its scale 1:`scale` for distances and 1:`height_scale` for heights."""

    game_width_km: float
    game_height_km: float
    scale: float
    height_scale: float

    @property
    def width_px(self) -> int:
        return round(self.game_width_km * 1000 / GAME_METERS_PER_PIXEL) + 1

    @property
    def height_px(self) -> int:
        return round(self.game_height_km * 1000 / GAME_METERS_PER_PIXEL) + 1

    @property
    def game_per_real(self) -> float:
        """Game metres per real metre of distance."""
        return 1 / self.scale

    @property
    def height_game_per_real(self) -> float:
        """Game metres per real metre of height."""
        return 1 / self.height_scale

    def to_json(self) -> dict[str, float]:
        return {"game_width_km": self.game_width_km, "game_height_km": self.game_height_km, "scale": self.scale,
                "height_scale": self.height_scale}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MapFormat:
        return cls(**{"height_scale": data["scale"], **data})



class Rotation(Enum):
    """Direction of the map's top edge, clockwise from north in degrees."""

    NORTH = 0
    EAST = 90
    SOUTH = 180
    WEST = 270

    @property
    def quarter_turns(self) -> int:
        """Counter-clockwise quarter turns applied to a geo raster (as np.rot90)."""
        return self.value // 90

    @property
    def is_sideways(self) -> bool:
        return self.quarter_turns % 2 == 1

    def to_game(self, raster: NDArray[Any]) -> NDArray[Any]:
        """Raster in geo orientation (rows north -> south) -> orientation on the game map."""
        if not self.quarter_turns:
            return raster
        return np.ascontiguousarray(np.rot90(raster, self.quarter_turns))

    def geo_shape(self, rows: int, cols: int) -> tuple[int, int]:
        """Shape of a geo raster that becomes rows x cols after `to_game`."""
        return (cols, rows) if self.is_sideways else (rows, cols)

    def geo_to_uv(self, fx: float, fy: float) -> tuple[float, float]:
        """Geo fractions within the crop -> map fractions (u left -> right, v top -> bottom)."""
        if self is Rotation.EAST:      # top = east: left = north
            return fy, 1 - fx
        if self is Rotation.SOUTH:
            return 1 - fx, 1 - fy
        if self is Rotation.WEST:      # top = west: left = south
            return 1 - fy, fx
        return fx, fy

    def game_extent(self, geo_width: float, geo_height: float) -> tuple[float, float]:
        return (geo_height, geo_width) if self.is_sideways else (geo_width, geo_height)


@dataclass(frozen=True)
class SourceBounds:
    """The crop in the coordinate system of the elevation model (degrees if `geographic`, else metres)."""

    left: float
    bottom: float
    right: float
    top: float
    geographic: bool

    def fractions(self, x: float, y: float) -> tuple[float, float]:
        """Point in the source CRS -> geo fractions (west -> east, north -> south)."""
        return (x - self.left) / (self.right - self.left), (self.top - y) / (self.top - self.bottom)

    def extent_km(self) -> tuple[float, float]:
        """Extent east-west x north-south in km."""
        if self.geographic:
            mid_lat = math.radians((self.top + self.bottom) / 2)
            return ((self.right - self.left) * _KM_PER_DEGREE_LON_AT_EQUATOR * math.cos(mid_lat),
                    (self.top - self.bottom) * _KM_PER_DEGREE_LAT)
        return (self.right - self.left) / 1000, (self.top - self.bottom) / 1000


@data_type("map_frame", "The heightmap grid over the real crop.", preview=lambda f: json.dumps(f.to_json(), indent=1),
           codec=JsonDataCodec(lambda data: MapFrame.from_json(data)))
@dataclass(frozen=True)
class MapFrame:
    """The heightmap grid (`width_px` x `height_px`, game orientation) over the real crop and its coordinate system.

    Rasters of the map carry the frame; a raster with fewer pixels covers the same map at a coarser resolution.
    """

    width_px: int
    height_px: int
    bounds: SourceBounds
    rotation: Rotation
    crs_wkt: str

    @property
    def extent_km(self) -> tuple[float, float]:
        """Real extent along the map width x map height in km."""
        return self.rotation.game_extent(*self.bounds.extent_km())

    def meters_per_pixel(self, width: int | None = None, height: int | None = None) -> tuple[float, float]:
        """Real metres per pixel along the map width and height, of the heightmap grid or of a grid width x height."""
        width_km, height_km = self.extent_km
        return width_km * 1000 / ((width or self.width_px) - 1), height_km * 1000 / ((height or self.height_px) - 1)

    @property
    def horizontal_scale(self) -> float:
        """Game metres per real metre, from the crop and the heightmap size."""
        mpp_x, mpp_y = self.meters_per_pixel()
        return GAME_METERS_PER_PIXEL / ((mpp_x + mpp_y) / 2)

    def game_to_real_m(self, game_m: float) -> float:
        """Game metres -> real metres (the same number of pixels, whatever the map scale)."""
        return game_m / self.horizontal_scale

    def work_grid(self, factor: int) -> tuple[int, int]:
        """Size (width, height) of a grid 1/factor of the heightmap resolution."""
        return math.ceil(self.width_px / factor), math.ceil(self.height_px / factor)

    def to_json(self) -> dict[str, Any]:
        b = self.bounds
        return {"width_px": self.width_px, "height_px": self.height_px, "rotation": self.rotation.value,
                "bounds": [b.left, b.bottom, b.right, b.top, b.geographic], "crs_wkt": self.crs_wkt}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MapFrame:
        return cls(data["width_px"], data["height_px"], SourceBounds(*data["bounds"]), Rotation(data["rotation"]),
                   data["crs_wkt"])



class MapFormatParams(NodeParams):
    game_width_km: float = param(8.192, gt=0, unit="km")
    """Width of the game map."""
    game_height_km: float = param(8.192, gt=0, unit="km")
    """Height of the game map."""
    scale: float = param(1.0, ge=1, le=10)
    """Scale 1:scale for distances - game metres per real metre = 1 / scale."""
    height_scale: float | None = param(None, gt=0, le=10)
    """Scale 1:height_scale for heights; None = like `scale`, 1 = real heights, below 1 = higher than real.
    Slopes in game get scale / height_scale times steeper."""


@node("map.format", output="format")
def map_format(params: MapFormatParams, context: NodeContext) -> MapFormat:
    """The game map's size and scales."""
    height_scale = params.scale if params.height_scale is None else params.height_scale
    if height_scale != params.scale:
        steeper = params.scale / height_scale
        context.log(f"heights 1:{height_scale:g}, distances 1:{params.scale:g} - slopes x{steeper:.2g} in game"
                    " (more bridges and tunnels, less building land for towns)")
    return MapFormat(params.game_width_km, params.game_height_km, params.scale, height_scale)


class MapProjection:
    """Projects WGS84 points onto the map (u left -> right, v top -> bottom, 0..1) through the frame's CRS."""

    def __init__(self, frame: MapFrame) -> None:
        self._frame = frame
        self._crs = CRS.from_wkt(frame.crs_wkt)

    def wgs84_box(self) -> tuple[float, float, float, float]:
        """The crop as (lon0, lat0, lon1, lat1)."""
        b = self._frame.bounds
        lon0, lat0, lon1, lat1 = transform_bounds(self._crs, "EPSG:4326", b.left, b.bottom, b.right, b.top)
        return lon0, lat0, lon1, lat1

    def ring_to_uv(self, ring: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
        """[(lon, lat), ...] -> [(u, v), ...]."""
        xs, ys = transform("EPSG:4326", self._crs, [p[0] for p in ring], [p[1] for p in ring])
        b, rotation = self._frame.bounds, self._frame.rotation
        return [rotation.geo_to_uv(*b.fractions(x, y)) for x, y in zip(xs, ys, strict=True)]
