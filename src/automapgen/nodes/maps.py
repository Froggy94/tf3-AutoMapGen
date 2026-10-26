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
from pydantic import BaseModel, ConfigDict, Field
from rasterio.crs import CRS
from rasterio.transform import Affine
from rasterio.warp import transform, transform_bounds

from automapgen.graph import NodeParams, data_type, node, param

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
    """Size of the game map in km and its scale 1:`scale` (game metres per real metre = 1 / scale)."""

    game_width_km: float
    game_height_km: float
    scale: float

    @property
    def width_px(self) -> int:
        return round(self.game_width_km * 1000 / GAME_METERS_PER_PIXEL) + 1

    @property
    def height_px(self) -> int:
        return round(self.game_height_km * 1000 / GAME_METERS_PER_PIXEL) + 1

    @property
    def game_per_real(self) -> float:
        return 1 / self.scale

    def to_json(self) -> dict[str, float]:
        return {"game_width_km": self.game_width_km, "game_height_km": self.game_height_km, "scale": self.scale}

    @classmethod
    def from_json(cls, data: dict[str, float]) -> MapFormat:
        return cls(**data)



class GeoCenter(BaseModel):
    """Centre of a metric map footprint in WGS84 degrees."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    lon: float = Field(ge=-180, le=180)
    """Longitude, east positive."""
    lat: float = Field(gt=-90, lt=90)
    """Latitude, north positive."""


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
    rotation: Rotation | float
    crs_wkt: str
    # Normalized map coordinates (u right, v down) -> projected metres. Absent for legacy crops.
    uv_transform: Affine | None = None

    @classmethod
    def centered(cls, map_format: MapFormat, center: GeoCenter, rotation_deg: float) -> MapFrame:
        """A metric rectangle, bearing clockwise from true north at its centre.

        AEQD preserves distances/bearings from the centre. The download envelope is not the map's extent.
        Heightmap samples span the rectangle including its endpoints (4 game metres between samples).
        """
        if not math.isfinite(rotation_deg):
            raise ValueError("rotation must be finite")
        angle = rotation_deg % 360
        theta = math.radians(angle)
        c, s = math.cos(theta), math.sin(theta)
        width = (map_format.width_px - 1) * GAME_METERS_PER_PIXEL * map_format.scale
        height = (map_format.height_px - 1) * GAME_METERS_PER_PIXEL * map_format.scale
        if width <= 0 or height <= 0:
            raise ValueError("map must have at least two samples on each axis")
        placement = Affine(width * c, -height * s, (-width * c + height * s) / 2,
                           -width * s, -height * c, (width * s + height * c) / 2)
        corners = [placement * uv for uv in ((0, 0), (1, 0), (1, 1), (0, 1))]
        xs, ys = zip(*corners, strict=True)
        bounds = SourceBounds(min(xs), min(ys), max(xs), max(ys), False)
        crs = CRS.from_dict(proj="aeqd", lat_0=center.lat, lon_0=center.lon, datum="WGS84", units="m")
        frame = cls(map_format.width_px, map_format.height_px, bounds, angle, crs.to_wkt(), placement)
        MapProjection(frame).wgs84_box()  # reject unsupported dateline-crossing selections early
        return frame

    @property
    def bearing_deg(self) -> float:
        return self.rotation.value if isinstance(self.rotation, Rotation) else self.rotation

    @property
    def cardinal_rotation(self) -> Rotation:
        """Quarter turn for the legacy crop path only."""
        return Rotation(self.bearing_deg)

    def raster_transform(self, width: int | None = None, height: int | None = None) -> Affine:
        """GDAL pixel-edge transform; pixel centres coincide with map samples, including u/v=0 and 1."""
        if self.uv_transform is None:
            raise ValueError("a sample transform requires a metric map frame")
        w, h = width or self.width_px, height or self.height_px
        dx, dy = 1 / max(w - 1, 1), 1 / max(h - 1, 1)
        # A one-sample work grid represents the centre, rather than the top/left edge.
        return self.uv_transform * Affine.translation(0.5 if w == 1 else 0, 0.5 if h == 1 else 0) * \
            Affine.scale(dx, dy) * Affine.translation(-0.5, -0.5)

    @property
    def extent_km(self) -> tuple[float, float]:
        """Real extent along the map width x map height in km (not its enclosing bounding box)."""
        if (t := self.uv_transform) is not None:
            return math.hypot(t.a, t.d) / 1000, math.hypot(t.b, t.e) / 1000
        return self.cardinal_rotation.game_extent(*self.bounds.extent_km())

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
        data = {"width_px": self.width_px, "height_px": self.height_px, "rotation": self.bearing_deg,
                "bounds": [b.left, b.bottom, b.right, b.top, b.geographic], "crs_wkt": self.crs_wkt}
        if self.uv_transform is not None:
            data["uv_transform"] = list(self.uv_transform)[:6]
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MapFrame:
        placement = Affine(*data["uv_transform"]) if "uv_transform" in data else None
        rotation = float(data["rotation"]) if placement is not None else Rotation(data["rotation"])
        return cls(data["width_px"], data["height_px"], SourceBounds(*data["bounds"]), rotation,
                   data["crs_wkt"], placement)



class MapFormatParams(NodeParams):
    game_width_km: float = param(8.192, gt=0, unit="km")
    """Width of the game map."""
    game_height_km: float = param(8.192, gt=0, unit="km")
    """Height of the game map."""
    scale: float = param(1.0, ge=1, le=10)
    """Scale 1:scale - game metres per real metre = 1 / scale (horizontal and vertical)."""


@node("map.format", output="format")
def map_format(params: MapFormatParams) -> MapFormat:
    """The game map's size and scale."""
    return MapFormat(params.game_width_km, params.game_height_km, params.scale)


class MapProjection:
    """Projects WGS84 points onto the map (u left -> right, v top -> bottom, 0..1) through the frame's CRS."""

    def __init__(self, frame: MapFrame) -> None:
        self._frame = frame
        self._crs = CRS.from_wkt(frame.crs_wkt)

    def wgs84_box(self) -> tuple[float, float, float, float]:
        """The crop as (lon0, lat0, lon1, lat1)."""
        if self._frame.uv_transform is not None:
            xs, ys = zip(*self.footprint(), strict=True)
            if max(xs) - min(xs) > 180:
                raise ValueError("map footprints crossing the antimeridian are not supported")
            return min(xs), min(ys), max(xs), max(ys)
        b = self._frame.bounds
        lon0, lat0, lon1, lat1 = transform_bounds(self._crs, "EPSG:4326", b.left, b.bottom, b.right, b.top)
        return lon0, lat0, lon1, lat1

    def uv_to_wgs84(self, points: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
        """Map fractions -> WGS84. The inverse of ring_to_uv for metric frames."""
        if not points:
            return []
        if self._frame.uv_transform is None:
            raise ValueError("inverse projection requires a metric map frame")
        xy = [self._frame.uv_transform * (p[0], p[1]) for p in points]
        xs, ys = transform(self._crs, "EPSG:4326", [p[0] for p in xy], [p[1] for p in xy])
        return list(zip(xs, ys, strict=True))

    def footprint(self, segments: int = 32) -> list[tuple[float, float]]:
        """Densified perimeter, so curved projected edges are included in geographic query bounds."""
        t = np.linspace(0, 1, segments + 1)
        uv = ([(v, 0.0) for v in t] + [(1.0, v) for v in t] +
              [(1 - v, 1.0) for v in t] + [(0.0, 1 - v) for v in t])
        return self.uv_to_wgs84(uv)

    def ring_to_uv(self, ring: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
        """[(lon, lat), ...] -> [(u, v), ...]."""
        if not ring:
            return []
        xs, ys = transform("EPSG:4326", self._crs, [p[0] for p in ring], [p[1] for p in ring])
        if self._frame.uv_transform is not None:
            inverse = ~self._frame.uv_transform
            return [inverse * (x, y) for x, y in zip(xs, ys, strict=True)]
        b, rotation = self._frame.bounds, self._frame.cardinal_rotation
        return [rotation.geo_to_uv(*b.fractions(x, y)) for x, y in zip(xs, ys, strict=True)]
