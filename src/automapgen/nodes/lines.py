"""Lines from OpenStreetMap for the terrain: field tracks and hiking paths, country roads - planned as map lines, then
worked into the terrain (no roads or paths are built here; other mods build them).

Planning: ways cut at the map edge, water, glaciers, town areas (and for minor ways forest and settlement), simplified,
short pieces dropped. Terrain: road level across with a smoothed, grade-limited profile and banks; paths as a smooth,
slightly lowered strip.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, NamedTuple

import numpy as np
from numpy.typing import NDArray
from PIL import Image as PilImage
from PIL import ImageDraw
from pydantic import Field as Setting
from scipy.ndimage import gaussian_filter1d, map_coordinates, uniform_filter

from automapgen.graph import NodeContext, NodeParams, data_type, node, param
from automapgen.nodes.documents import JsonDocument, JsonDocumentCodec
from automapgen.nodes.images import Image
from automapgen.nodes.maps import GAME_METERS_PER_PIXEL, MapFormat, MapFrame, MapProjection, Rotation, SourceBounds
from automapgen.nodes.osm import OsmLines, OsmPoints
from automapgen.nodes.rasters import Field, Mask

_TOWN_GRID_M = 2000.0
_TOWN_RADIUS_M = {"city": 1500.0, "town": 800.0, "village": 350.0, "hamlet": 120.0}
_PREVIEW_WATER = (40, 110, 230)
_PREVIEW_WATER_GREY = 100
FULL_WEIGHT = 0.999              # road core: pixels at (almost) full weight
_CLASS_COLORS = {"trunk": (200, 0, 0), "primary": (240, 90, 0), "secondary": (250, 170, 0),
                 "tertiary": (255, 230, 60), "unclassified": (255, 255, 255)}

type Point = tuple[float, float]                 # map fractions (u, v)
type Points = NDArray[np.float64]
type FloatMap = NDArray[np.float32]
type BoolMap = NDArray[np.bool_]


@data_type("path_plan", "Field tracks and hiking paths: {track: [lines], trail: [lines]}, lines in map fractions.",
           codec=JsonDocumentCodec(lambda data: PathPlan(data)),
           preview=lambda p: f"{len(p.data['track'])} tracks, {len(p.data['trail'])} trails")
@dataclass(frozen=True)
class PathPlan(JsonDocument):
    pass


@data_type("road_lines", "Country roads to work into the terrain: {roads: [{cls, pts}]}, points in map fractions.",
           codec=JsonDocumentCodec(lambda data: RoadLines(data)), preview=lambda r: f"{len(r.data['roads'])} roads")
@dataclass(frozen=True)
class RoadLines(JsonDocument):
    pass


# --- shared planning helpers ---------------------------------------------------------------------------------------


def extent_m(bounds: SourceBounds, rotation: Rotation) -> tuple[float, float]:
    """Real extent map width x map height in metres (formula of the line planners)."""
    if bounds.geographic:
        mid_lat = math.radians((bounds.top + bounds.bottom) / 2)
        width_m = (bounds.right - bounds.left) * 111320 * math.cos(mid_lat)
        height_m = (bounds.top - bounds.bottom) * 110570
    else:
        width_m, height_m = bounds.right - bounds.left, bounds.top - bounds.bottom
    return rotation.game_extent(width_m, height_m)


def node_positions(lines: OsmLines, projection: MapProjection) -> dict[int, Point]:
    """Map fractions of all OSM nodes."""
    ids = list(lines.points)
    return dict(zip(ids, projection.ring_to_uv([lines.points[i] for i in ids]), strict=True))


class TownAreas:
    """Circles around OSM places (radius per place kind, real metres); lookup through a coarse grid."""

    def __init__(self, circles: Sequence[tuple[float, float, float]], extent: tuple[float, float]) -> None:
        self.circles = list(circles)
        self._width_m, self._height_m = extent
        self._grid: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
        cell = _TOWN_GRID_M
        for x, y, r in self.circles:
            for gx in range(int((x - r) // cell), int((x + r) // cell) + 1):
                for gy in range(int((y - r) // cell), int((y + r) // cell) + 1):
                    self._grid.setdefault((gx, gy), []).append((x, y, r))

    @classmethod
    def from_places(cls, places: OsmPoints, radius_m: Mapping[str, float], projection: MapProjection,
                    extent: tuple[float, float]) -> TownAreas:
        wanted = [p for p in places.points if radius_m.get(p.tags.get("place", ""), 0) > 0]
        uvs = projection.ring_to_uv([(p.lon, p.lat) for p in wanted]) if wanted else []
        width_m, height_m = extent
        circles = [(u * width_m, v * height_m, radius_m[p.tags["place"]])
                   for p, (u, v) in zip(wanted, uvs, strict=True)]
        return cls(circles, extent)

    def contains(self, p: Point) -> bool:
        x, y = p[0] * self._width_m, p[1] * self._height_m
        cell = _TOWN_GRID_M
        return any((x - cx) ** 2 + (y - cy) ** 2 < r * r for cx, cy, r in self._grid.get((int(x // cell),
                                                                                           int(y // cell)), ()))


class MaskLookup:
    """A boolean raster over the map, looked up at map fractions."""

    def __init__(self, mask: BoolMap) -> None:
        self._mask = mask
        self._h, self._w = mask.shape

    def at(self, p: Point) -> bool:
        u, v = p
        return bool(self._mask[min(int(v * self._h), self._h - 1), min(int(u * self._w), self._w - 1)])


class LineCutter:
    """Densify a line, split it into runs of free points, simplify each run (Douglas-Peucker)."""

    def __init__(self, extent: tuple[float, float], densify_m: float) -> None:
        self._width_m, self._height_m = extent
        self._densify_m = densify_m
        self.scale = np.array([self._width_m, self._height_m])

    def densify(self, pts: Sequence[Point]) -> list[Point]:
        """Extra points every densify_m, so area borders take effect precisely."""
        out = [pts[0]]
        for a, b in itertools.pairwise(pts):
            d = math.hypot((b[0] - a[0]) * self._width_m, (b[1] - a[1]) * self._height_m)
            n = max(1, int(d // self._densify_m))
            out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]
        return out

    @staticmethod
    def runs(points: Iterable[Point | None], free: Callable[[Point], bool]) -> list[list[Point]]:
        """Pieces of at least two consecutive free points."""
        runs: list[list[Point]] = []
        run: list[Point] = []
        for p in points:
            if p is not None and free(p):
                run.append(p)
            else:
                if len(run) >= 2:
                    runs.append(run)
                run = []
        if len(run) >= 2:
            runs.append(run)
        return runs

    @staticmethod
    def simplify(p: Points, tol: float) -> Points:
        keep = np.zeros(len(p), bool)
        keep[[0, -1]] = True
        stack = [(0, len(p) - 1)]
        while stack:
            a, b = stack.pop()
            if b <= a + 1:
                continue
            d = p[b] - p[a]
            n = np.hypot(*d)
            seg = p[a + 1:b] - p[a]
            dist = np.abs(seg[:, 0] * d[1] - seg[:, 1] * d[0]) / n if n > 0 else np.hypot(*seg.T)
            k = int(np.argmax(dist))
            if dist[k] > tol:
                keep[a + 1 + k] = True
                stack += [(a, a + 1 + k), (a + 1 + k, b)]
        result: Points = p[keep]
        return result


def _blocked(masks: Sequence[Mask]) -> MaskLookup:
    if not masks:
        return MaskLookup(np.zeros((2, 2), bool))
    values = masks[0].values
    for m in masks[1:]:
        values = values | m.values
    return MaskLookup(values)


# --- field tracks and hiking paths ---------------------------------------------------------------------------------


class PathParams(NodeParams):
    track_types: tuple[str, ...] = ("track",)
    """OSM highway values of field and forest tracks."""
    trail_types: tuple[str, ...] = ("path", "footway", "bridleway")
    """OSM highway values of hiking paths."""
    only_paved: bool = True
    """Only paved ways (track grades or paved surfaces)."""
    track_grades: tuple[str, ...] = ("grade1", "grade2", "grade3")
    """tracktype values that count as paved."""
    paved_surfaces: tuple[str, ...] = ("paved", "asphalt", "concrete", "paving_stones", "sett", "compacted",
                                       "fine_gravel", "gravel")
    """surface values that count as paved."""
    skip_private: bool = True
    """Leave out access=private/no."""
    trail_sac_scales: tuple[str, ...] = ("hiking", "mountain_hiking", "demanding_mountain_hiking", "alpine_hiking")
    """Mountain trails with these sac_scale values always (any surface, also in forest and settlement)."""
    main_track_grades: tuple[str, ...] = ("grade1",)
    """In forest and settlement only main tracks: these grades ..."""
    main_named: bool = True
    """... or named tracks ..."""
    main_surfaces: tuple[str, ...] = ("paved", "asphalt", "concrete", "paving_stones", "sett")
    """... or these surfaces (trails never)."""
    town_radius_m: dict[str, float] = Setting(default_factory=lambda: dict(_TOWN_RADIUS_M))
    """Built-up area = circle around OSM places per kind (real metres); {} = none."""
    densify_m: float = param(10.0, gt=0, unit="m")
    """Sample the ways this finely before cutting (real metres)."""
    simplify_m: float = param(4.0, ge=0, unit="m")
    """Simplification (real metres, about half the heightmap resolution)."""
    min_length_m: float = param(60.0, ge=0, unit="m")
    """Shorter pieces are left out (real metres)."""


@node("paths.plan", output="paths")
def plan_paths(ways: OsmLines, places: OsmPoints, frame: MapFrame, blocked: tuple[Mask, ...], restricted: Mask,
               params: PathParams, context: NodeContext) -> PathPlan:
    """Field tracks (only paved; in forest and settlement only main tracks) and hiking paths (mountain trails always,
    others only paved) from OSM; cut at the map edge, at `blocked` (water, glacier) and - except main ways - in
    `restricted` (forest, settlement) and town areas."""
    projection = MapProjection(frame)
    extent = extent_m(frame.bounds, frame.rotation)
    towns = TownAreas.from_places(places, params.town_radius_m, projection, extent) if params.town_radius_m \
        else TownAreas([], extent)
    planner = _PathPlanner(params, LineCutter(extent, params.densify_m), towns, _blocked(blocked),
                           MaskLookup(restricted.values), context)
    return planner.plan(ways, node_positions(ways, projection))


class _PathPlanner:
    def __init__(self, params: PathParams, cutter: LineCutter, towns: TownAreas, blocked: MaskLookup,
                 restricted: MaskLookup, context: NodeContext) -> None:
        self._p = params
        self._cutter = cutter
        self._towns = towns
        self._blocked = blocked
        self._restricted = restricted
        self._context = context

    def plan(self, ways: OsmLines, uv: Mapping[int, Point]) -> PathPlan:
        p = self._p
        result: dict[str, list[Points]] = {"track": [], "trail": []}
        stats = {"track": [0, 0.0], "trail": [0, 0.0]}
        dropped = 0
        scale = self._cutter.scale
        for w in ways.lines:
            tags = w.tags
            kind = "track" if tags.get("highway") in p.track_types else "trail"
            if p.only_paved and not self._paved(tags) and not self._mountain_trail(kind, tags):
                continue
            if p.skip_private and tags.get("access") in ("private", "no"):
                continue
            main = self._is_main(kind, tags)
            pts = [uv.get(n) for n in w.nodes]
            points = (self._cutter.densify([q for q in pts if q is not None]) if None not in pts and len(pts) >= 2
                      else pts)
            for run in self._cutter.runs(points, partial(self._free, main=main)):
                line = self._cutter.simplify(np.array(run) * scale, p.simplify_m)
                length = np.hypot(*np.diff(line, axis=0).T).sum()
                if length < p.min_length_m:
                    dropped += 1
                    continue
                result[kind].append(line / scale)
                stats[kind][0] += 1
                stats[kind][1] += length
        self._context.log(f"field tracks: {stats['track'][0]} pieces, {stats['track'][1] / 1000:.0f} km; hiking "
                          f"paths: {stats['trail'][0]} pieces, {stats['trail'][1] / 1000:.0f} km; left out (too "
                          f"short): {dropped}")
        return PathPlan({k: [[[round(float(u), 7), round(float(v), 7)] for u, v in line] for line in lines]
                         for k, lines in result.items()})

    def _paved(self, tags: Mapping[str, str]) -> bool:
        return tags.get("tracktype") in self._p.track_grades or tags.get("surface") in self._p.paved_surfaces

    def _mountain_trail(self, kind: str, tags: Mapping[str, str]) -> bool:
        return kind == "trail" and tags.get("sac_scale") in self._p.trail_sac_scales

    def _is_main(self, kind: str, tags: Mapping[str, str]) -> bool:
        p = self._p
        if self._mountain_trail(kind, tags):
            return True
        return kind == "track" and (tags.get("tracktype") in p.main_track_grades or tags.get("surface")
                                    in p.main_surfaces or (p.main_named and "name" in tags))

    def _free(self, p: Point, main: bool) -> bool:
        u, v = p
        if not (0 <= u <= 1 and 0 <= v <= 1):
            return False
        if self._blocked.at(p):
            return False
        if main:
            return True
        if self._restricted.at(p):
            return False
        return not self._towns.contains(p)


# --- country roads -------------------------------------------------------------------------------------------------


class RoadParams(NodeParams):
    road_types: tuple[str, ...] = ("trunk", "primary", "secondary", "tertiary", "unclassified")
    """OSM highway classes (widths: the road terrain node)."""
    include_links: bool = True
    """Links (<class>_link) count as the class."""
    skip_bridges: bool = True
    """Leave out bridge=* (except no) - else a dam across the valley."""
    skip_tunnels: bool = True
    """Leave out tunnel=* / covered=yes - else a trench over the mountain."""
    town_radius_m: dict[str, float] = Setting(default_factory=lambda: dict(_TOWN_RADIUS_M))
    """Left out inside towns: circle per place kind (real metres, 0 = none)."""
    settlement_radius_m: float = param(100.0, gt=0, unit="m")
    """Closed settlement only: share of WorldCover settlement within this radius (real metres) ..."""
    settlement_share: float = param(0.5, ge=0, le=1)
    """... at least this (roads themselves are often settlement in WorldCover)."""
    densify_m: float = param(10.0, gt=0, unit="m")
    """Sample the roads this finely before cutting (real metres)."""
    simplify_m: float = param(4.0, ge=0, unit="m")
    """Simplification (real metres)."""
    min_length_m: float = param(80.0, ge=0, unit="m")
    """Shorter pieces are dropped after merging (real metres)."""


@node("roads.from_osm", output="roads")
def roads_from_osm(ways: OsmLines, places: OsmPoints, frame: MapFrame, water: Mask, settlement: Mask,
                   params: RoadParams, context: NodeContext) -> RoadLines:
    """Country roads for the terrain: OSM roads of the classes, joined where OSM splits them, cut at the map edge,
    bridges/tunnels, town areas, closed settlement (`settlement` = WorldCover settlement) and water."""
    projection = MapProjection(frame)
    extent = extent_m(frame.bounds, frame.rotation)
    towns = TownAreas.from_places(places, params.town_radius_m, projection, extent)
    size = max(1, round(2 * params.settlement_radius_m / (extent[0] / settlement.values.shape[1])))
    dense = uniform_filter(settlement.values.astype(np.float32), size) >= params.settlement_share
    planner = _RoadPlanner(params, LineCutter(extent, params.densify_m), towns, MaskLookup(water.values),
                           MaskLookup(dense), context)
    return planner.plan(ways, node_positions(ways, projection))


def merge_ways(ways: Sequence[tuple[str, Sequence[int]]]) -> list[tuple[str, list[int]]]:
    """OSM splits roads at every junction/property change: ways of the same class meeting at an end where no other
    way of this class ends are joined into one line (continuous longitudinal profile)."""
    out = []
    by_cls: dict[str, list[list[int]]] = {}
    for cls, nd in ways:
        by_cls.setdefault(cls, []).append(list(nd))
    for cls, lines in by_cls.items():
        ends: dict[int, list[int]] = {}
        for i, nd in enumerate(lines):
            for e in (nd[0], nd[-1]):
                ends.setdefault(e, []).append(i)
        used = [False] * len(lines)
        for i in range(len(lines)):
            if used[i]:
                continue
            used[i] = True
            chain = lines[i]
            for at_end in (True, False):          # first extend at the end, then at the start
                while True:
                    e = chain[-1] if at_end else chain[0]
                    nxt = [j for j in ends.get(e, ()) if not used[j]]
                    if len(ends.get(e, ())) != 2 or len(nxt) != 1 or chain[0] == chain[-1]:
                        break
                    j = nxt[0]
                    used[j] = True
                    seg = lines[j] if lines[j][0] == e else lines[j][::-1]
                    if not at_end:
                        seg = seg[::-1]
                    chain = chain + seg[1:] if at_end else seg[:-1] + chain
            out.append((cls, chain))
    return out


class _RoadPlanner:
    def __init__(self, params: RoadParams, cutter: LineCutter, towns: TownAreas, water: MaskLookup,
                 settlement: MaskLookup, context: NodeContext) -> None:
        self._p = params
        self._cutter = cutter
        self._towns = towns
        self._water = water
        self._settlement = settlement
        self._context = context

    def plan(self, ways: OsmLines, uv: Mapping[int, Point]) -> RoadLines:
        p = self._p
        types = set(p.road_types) | ({t + "_link" for t in p.road_types} if p.include_links else set())
        skipped = {"bridge": 0, "tunnel": 0, "short": 0}
        kept = []
        for w in ways.lines:
            if w.tags.get("highway") not in types:
                continue
            kind = self._structure(w.tags)
            if kind:
                skipped[kind] += 1
                continue
            kept.append((w.tags.get("highway", "").removesuffix("_link"), w.nodes))
        merged = merge_ways(kept)
        roads: list[dict[str, Any]] = []
        scale = self._cutter.scale
        for cls, nd in merged:
            found = [uv.get(n) for n in nd]
            pts = [q for q in found if q is not None]
            if len(pts) < len(found) or len(pts) < 2:
                continue
            for run in self._cutter.runs(self._cutter.densify(pts), self._free):
                line = self._cutter.simplify(np.array(run) * scale, p.simplify_m)
                length = np.hypot(*np.diff(line, axis=0).T).sum()
                if length < p.min_length_m:
                    skipped["short"] += 1
                    continue
                roads.append({"cls": cls, "pts": [[round(float(u), 7), round(float(v), 7)] for u, v in line / scale]})
        self._context.log(f"{len(kept)} OSM ways joined into {len(merged)} lines, {len(roads)} pieces kept; left out: "
                          f"{skipped['bridge']} bridges, {skipped['tunnel']} tunnels, {skipped['short']} short pieces")
        return RoadLines({"roads": roads})

    def _structure(self, tags: Mapping[str, str]) -> str | None:
        if self._p.skip_bridges and tags.get("bridge", "no") != "no":
            return "bridge"
        if self._p.skip_tunnels and (tags.get("tunnel", "no") != "no" or tags.get("covered") == "yes"):
            return "tunnel"
        return None

    def _free(self, p: Point) -> bool:
        u, v = p
        if not (0 <= u <= 1 and 0 <= v <= 1):
            return False
        if self._water.at(p):
            return False
        if self._settlement.at(p):
            return False
        return not self._towns.contains(p)


# --- previews ------------------------------------------------------------------------------------------------------


class BaseParams(NodeParams):
    width: int = param(2000, ge=100)
    """Width of the preview picture in pixels."""


@node("lines.preview_base", output="image")
def preview_base(biome_preview: Image, water: Mask, heightmap: Image, params: BaseParams) -> Image:
    """Biome preview at `width`, the painted water layer and the real water (black in the heightmap) in blue."""
    img = PilImage.fromarray(biome_preview.pixels).convert("RGB")
    height = round(params.width * img.size[1] / img.size[0])
    img = img.resize((params.width, height), PilImage.Resampling.BILINEAR)
    for mask in (water.values, heightmap.pixels == 0):
        grey = PilImage.fromarray(mask.astype(np.uint8) * 255).resize((params.width, height),
                                                                      PilImage.Resampling.BILINEAR)
        img.paste(_PREVIEW_WATER, mask=grey.point(lambda g: 255 if g > _PREVIEW_WATER_GREY else 0))
    return Image(np.asarray(img))


class PathPreviewParams(NodeParams):
    track_color: tuple[int, int, int] = (140, 85, 30)
    """Colour of field tracks."""
    trail_color: tuple[int, int, int] = (255, 150, 0)
    """Colour of hiking paths."""


@node("paths.preview", output="image")
def preview_paths(base: Image, paths: PathPlan, params: PathPreviewParams) -> Image:
    """Tracks brown, trails orange on the preview base."""
    img = PilImage.fromarray(base.pixels)
    pw, ph = img.size
    draw = ImageDraw.Draw(img)
    for kind, color in (("trail", params.trail_color), ("track", params.track_color)):
        for line in paths.data[kind]:
            draw.line([(u * pw, v * ph) for u, v in line], fill=color, width=1)
    return Image(np.asarray(img))


class RoadPreviewParams(NodeParams):
    line_width: int = param(2, ge=1)
    """Line width."""
    road_types: tuple[str, ...] = ("trunk", "primary", "secondary", "tertiary", "unclassified")
    """Drawing order: the first on top."""


@node("roads.preview", output="image")
def preview_roads(base: Image, roads: RoadLines, params: RoadPreviewParams) -> Image:
    """Roads by class on the preview base (important roads on top)."""
    img = PilImage.fromarray(base.pixels)
    pw, ph = img.size
    draw = ImageDraw.Draw(img)
    for cls in reversed(params.road_types):
        for r in roads.data["roads"]:
            if r["cls"] == cls:
                line = [(u * pw, v * ph) for u, v in r["pts"]]
                draw.line(line, fill=(0, 0, 0), width=params.line_width + 2)
                draw.line(line, fill=_CLASS_COLORS.get(cls, (255, 255, 255)), width=params.line_width)
    return Image(np.asarray(img))


# --- terrain under the lines ---------------------------------------------------------------------------------------


@dataclass
class _Samples:
    """Samples every pixel along all lines: position, target height, half width, blend width."""

    x: list[NDArray[np.float64]]
    y: list[NDArray[np.float64]]
    target: list[NDArray[np.float64]]
    half: list[NDArray[np.float64]]
    blend: list[NDArray[np.float64]]


def road_profile(h: NDArray[np.float64], step_m: float, smooth_px: float, max_grade: float) -> NDArray[np.float64]:
    """Longitudinal profile: smoothed, then grade <= max_grade (mean of the upper and lower Lipschitz envelope)."""
    t = gaussian_filter1d(h, smooth_px, mode="nearest") if smooth_px > 0 else h.copy()
    d = max_grade * step_m
    up, lo = t.copy(), t.copy()
    for i in range(1, len(t)):        # upper envelope: min(t_j + d|i-j|), lower: max(t_j - d|i-j|)
        up[i] = min(up[i], up[i - 1] + d)
        lo[i] = max(lo[i], lo[i - 1] - d)
    for i in range(len(t) - 2, -1, -1):
        up[i] = min(up[i], up[i + 1] + d)
        lo[i] = max(lo[i], lo[i + 1] - d)
    result: NDArray[np.float64] = (up + lo) / 2
    return result


class LineStamp:
    """Samples lines every pixel and stamps target heights around each sample: weight 1 on the line, falling to 0
    over the blend width (cosine for roads, linear for paths); overlapping stamps are averaged by weight."""

    def __init__(self, dem: FloatMap) -> None:
        self._dem = dem
        self._height, self._width = dem.shape

    def sample(self, line: Sequence[Sequence[float]]) -> tuple[NDArray[np.float64], NDArray[np.float64],
                                                                NDArray[np.float64]] | None:
        """Positions (x, y) and terrain heights every pixel along the line, None if shorter than 2 pixels."""
        p = np.array(line) * [self._width, self._height] - 0.5
        seg = np.hypot(*np.diff(p, axis=0).T)
        dist = np.concatenate([[0], np.cumsum(seg)])
        if dist[-1] < 2:
            return None
        s = np.arange(0, dist[-1], 1.0)
        x, y = np.interp(s, dist, p[:, 0]), np.interp(s, dist, p[:, 1])
        return x, y, map_coordinates(self._dem, [y, x], order=1, mode="nearest")

    def stamp(self, x: FloatMap, y: FloatMap, target: FloatMap, half: FloatMap, blend: FloatMap | float,
              cosine: bool) -> tuple[FloatMap, FloatMap]:
        """Weighted target height and maximum weight per pixel. Samples are stamped in groups of the same reach, so a
        few wide samples (a deep cut) do not widen the stamp of all others."""
        width, height = self._width, self._height
        n_px = width * height
        sums = _StampSums(n_px)
        half_all = np.broadcast_to(np.asarray(half, dtype=np.float64), x.shape)
        blend_all = np.broadcast_to(np.asarray(blend, dtype=np.float64), x.shape)
        reach_all = np.ceil(half_all + blend_all).astype(np.int64)
        for reach in np.unique(reach_all).tolist():
            group = reach_all == reach
            gx, gy, gt, gh, gb = x[group], y[group], target[group], half_all[group], blend_all[group]
            cx, cy = np.round(gx).astype(np.int64), np.round(gy).astype(np.int64)
            for dy in range(-reach, reach + 1):
                for dx in range(-reach, reach + 1):
                    qx, qy = cx + dx, cy + dy
                    d = np.hypot(qx - gx, qy - gy)
                    w = np.clip((gh + gb - d) / gb, 0, 1)
                    ok = (w > 0) & (qx >= 0) & (qx < width) & (qy >= 0) & (qy < height)
                    wk = w[ok]
                    if cosine:
                        wk = 0.5 - 0.5 * np.cos(np.pi * wk)
                    sums.add(qy[ok] * width + qx[ok], wk, gt[ok])
        tsum, wsum, wmax = sums.totals()
        weighted = (tsum / np.maximum(wsum, 1e-9)).reshape(height, width).astype(np.float32)
        return weighted, wmax.reshape(height, width)


class _StampSums:
    """Per pixel: sum of weight x target, sum of weights, largest weight. Contributions are collected and added up in
    batches (one pass over all pixels per batch, not per stamp offset)."""

    BATCH = 4_000_000

    def __init__(self, n_px: int) -> None:
        self._n = n_px
        self._tsum = np.zeros(n_px, np.float64)
        self._wsum = np.zeros(n_px, np.float64)
        self._wmax = np.zeros(n_px, np.float32)
        self._parts: list[tuple[NDArray[np.int64], NDArray[np.float64], NDArray[np.float64]]] = []
        self._size = 0

    def add(self, flat: NDArray[np.int64], weight: NDArray[Any], target: NDArray[Any]) -> None:
        self._parts.append((flat, weight, target))
        self._size += len(flat)
        if self._size >= self.BATCH:
            self._flush()

    def totals(self) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float32]]:
        self._flush()
        return self._tsum, self._wsum, self._wmax

    def _flush(self) -> None:
        if not self._parts:
            return
        flat = np.concatenate([p[0] for p in self._parts])
        weight = np.concatenate([p[1] for p in self._parts])
        target = np.concatenate([p[2] for p in self._parts])
        self._tsum += np.bincount(flat, weights=weight * target, minlength=self._n)
        self._wsum += np.bincount(flat, weights=weight, minlength=self._n)
        np.maximum.at(self._wmax, flat, weight.astype(np.float32))
        self._parts, self._size = [], 0


def _concat(parts: Sequence[NDArray[np.float64]]) -> FloatMap:
    return np.concatenate(parts).astype(np.float32)


class RoadTerrainParams(NodeParams):
    width_game_m: dict[str, float] = Setting(default_factory=lambda: {"trunk": 12.0, "primary": 10.0, "secondary": 9.0,
                                                                    "tertiary": 8.0, "unclassified": 6.0})
    """Carriageway + shoulder per road class (game m)."""
    default_width_game_m: float = param(6.0, gt=0, unit="game m")
    """Width of other classes."""
    sink_game_m: float = param(0.2, unit="game m")
    """Lower the road by this much."""
    profile_smooth_game_m: float = param(40.0, ge=0, unit="game m")
    """Smooth the longitudinal profile."""
    max_grade: float = param(0.12, gt=0)
    """Steepest grade along the road (height / distance)."""
    max_change_game_m: float = param(3.0, ge=0, unit="game m")
    """Change the terrain at most this much."""
    bank_slope: float = param(0.6, gt=0)
    """Bank slope (height / width); smaller = flatter, wider bank."""
    blend_min_px: float = param(1.5, gt=0)
    """Transition to the side at least (heightmap pixels)."""


class RoadTerrain(NamedTuple):
    heights: Field
    core: Mask


@node("roads.terrain")
def road_terrain(heights: Field, roads: RoadLines, keep_free: Mask, map_format: MapFormat, params: RoadTerrainParams,
                 context: NodeContext) -> RoadTerrain:
    """Terrain under the country roads: level across, profile smoothed and grade-limited, cut/fill with banks (width =
    change / bank slope); `keep_free` (water and its border) stays as it is. `core` = pixels at full weight."""
    p, scale, px = params, map_format.height_game_per_real, GAME_METERS_PER_PIXEL
    dem = heights.values
    px_real_m = px / scale                     # real height metres of a game grade of 1 over one heightmap pixel
    sink_m, max_change_m = p.sink_game_m / scale, p.max_change_game_m / scale
    stamp = LineStamp(dem)
    samples = _Samples([], [], [], [], [])
    for road in roads.data["roads"]:
        sampled = stamp.sample(road["pts"])
        if sampled is None:
            continue
        x, y, h = sampled
        t = road_profile(h, px_real_m, p.profile_smooth_game_m / px, p.max_grade) - sink_m
        t = h + np.clip(t - h, -max_change_m, max_change_m)
        samples.x.append(x)
        samples.y.append(y)
        samples.target.append(t)
        samples.half.append(np.full(len(x), p.width_game_m.get(road["cls"], p.default_width_game_m) / px / 2))
        samples.blend.append(np.maximum(p.blend_min_px, np.abs(t - h) / p.bank_slope / px_real_m))
    if not samples.x:
        return RoadTerrain(heights, Mask(np.zeros(dem.shape, dtype=bool), heights.frame))
    weighted, wmax = stamp.stamp(_concat(samples.x), _concat(samples.y), _concat(samples.target),
                                 _concat(samples.half), _concat(samples.blend), cosine=True)
    wmax[keep_free.values] = 0                 # water and shore stay levelled
    change = np.clip(wmax * (weighted - dem), -max_change_m, max_change_m)
    sel = wmax > 0
    context.log(f"{int(sel.sum())} pixels adjusted, change {float(change[sel].min()):.1f} to "
                f"{float(change[sel].max()):.1f} m real")
    return RoadTerrain(heights.like(dem + change), Mask(wmax >= FULL_WEIGHT, heights.frame))


class PathTerrainParams(NodeParams):
    width_game_m: dict[str, float] = Setting(default_factory=lambda: {"track": 6.0, "trail": 4.0})
    """Width per path kind (game m)."""
    sink_game_m: float = param(0.25, unit="game m")
    """Lower the path by this much."""
    profile_smooth_game_m: float = param(20.0, ge=0, unit="game m")
    """Smooth the longitudinal profile of the path."""
    max_change_game_m: float = param(1.5, ge=0, unit="game m")
    """Change the terrain at most this much."""
    blend_px: float = param(1.5, gt=0)
    """Transition to the side (heightmap pixels)."""


@node("paths.terrain", output="heights")
def path_terrain(heights: Field, paths: PathPlan, keep_free: Mask, road_core: Mask, map_format: MapFormat,
                 params: PathTerrainParams, context: NodeContext) -> Field:
    """Terrain under the paths: level across (terrace on slopes, slight hollow), smooth transition to the side; water
    and its border (`keep_free`) and the country roads (`road_core`) stay as they are."""
    p, scale, px = params, map_format.height_game_per_real, GAME_METERS_PER_PIXEL
    dem = heights.values
    sink_m, max_change_m = p.sink_game_m / scale, p.max_change_game_m / scale
    stamp = LineStamp(dem)
    samples = _Samples([], [], [], [], [])
    for kind in ("track", "trail"):
        half = p.width_game_m[kind] / px / 2
        for line in paths.data[kind]:
            sampled = stamp.sample(line)
            if sampled is None:
                continue
            x, y, h = sampled
            t = gaussian_filter1d(h, p.profile_smooth_game_m / px, mode="nearest") - sink_m
            t = h + np.clip(t - h, -max_change_m, max_change_m)
            samples.x.append(x)
            samples.y.append(y)
            samples.target.append(t)
            samples.half.append(np.full(len(x), half))
    if not samples.x:
        return heights
    weighted, wmax = stamp.stamp(_concat(samples.x), _concat(samples.y), _concat(samples.target),
                                 _concat(samples.half), p.blend_px, cosine=False)
    wmax[keep_free.values] = 0                 # water and shore stay levelled
    wmax[road_core.values] = 0                 # country roads take precedence
    change = np.clip(wmax * (weighted - dem), -max_change_m, max_change_m)
    context.log(f"{int((wmax > 0).sum())} pixels adjusted")
    return heights.like(dem + change)


class GapParams(NodeParams):
    width_game_m: float = param(0.0, ge=0, unit="game m")
    """Width of the strip (0 = none)."""


@node("lines.strip", output="mask")
def strip(lines: JsonDocument, frame_of: Mask, params: GapParams) -> Mask:
    """The lines of a path plan or road plan drawn as a strip of the given width (e.g. a gap in the forest along
    paths); empty for width 0."""
    width, height = frame_of.frame.width_px, frame_of.frame.height_px
    canvas = PilImage.new("L", (width, height), 0)
    w = round(params.width_game_m / GAME_METERS_PER_PIXEL)
    if w > 0:
        draw = ImageDraw.Draw(canvas)
        data = lines.data
        all_lines = ([r["pts"] for r in data["roads"]] if "roads" in data
                     else [line for kind in ("track", "trail") for line in data[kind]])
        for line in all_lines:
            draw.line([(u * width - 0.5, v * height - 0.5) for u, v in line], fill=255, width=w, joint="curve")
    return Mask(np.asarray(canvas) > 0, frame_of.frame)
