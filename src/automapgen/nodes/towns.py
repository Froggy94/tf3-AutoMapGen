"""Towns at real places from OpenStreetMap, and the terrain evened out around towns (and industries).

Towns: largest place first, on buildable land, at a minimum distance; start size from the population. Positions in
game metres, map centre = (0, 0), north = +y (format of the map editor's import, ImportExportTownsIndustriesSettings).
Settling: a tilted plane around towns, a level area around industries, before the water is shaped.
"""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
from numpy.typing import NDArray
from PIL import Image as PilImage
from PIL import ImageDraw
from pydantic import Field as Setting
from scipy.ndimage import binary_dilation, convolve, distance_transform_edt, gaussian_filter, map_coordinates

from automapgen.graph import NodeContext, NodeParams, data_type, node, param
from automapgen.nodes.documents import JsonDocument, JsonDocumentCodec
from automapgen.nodes.images import Image
from automapgen.nodes.maps import GAME_METERS_PER_PIXEL, MapFormat, MapFrame, MapProjection
from automapgen.nodes.osm import OsmPoints
from automapgen.nodes.rasters import Field, Mask, resize_floats
from automapgen.nodes.rivers import preview_font

_LAYER_LIMIT = 0.3                    # a grid cell is blocked from this share of a layer
_PLANE_SAMPLE_PIXELS = 4000           # sample size for the best-fit plane of a town
_MIN_CORE_PIXELS = 10
_PREVIEW_MAX_WIDTH = 1600
_PREVIEW_FULL_COLOR_M = 5             # game metres of change for full colour in the settle preview

type BoolMap = NDArray[np.bool_]
type FloatMap = NDArray[np.float32]


class _TextCodec:
    suffix = ".txt"

    def save(self, value: TextDocument, path: Path) -> None:
        path.write_text(value.text, encoding="utf-8")

    def load(self, path: Path) -> TextDocument:
        return TextDocument(path.read_text(encoding="utf-8"))


@data_type("text_document", "A text file's content (e.g. a Lua import file for the map editor).", codec=_TextCodec(),
           preview=lambda t: t.text[:4000])
@dataclass(frozen=True)
class TextDocument:
    text: str


@data_type("town_list", "Planned towns: [{name, x, y, capacity}], game metres from the map centre, north = +y.",
           codec=JsonDocumentCodec(lambda data: TownList(data)),
           preview=lambda t: ", ".join(f"{town['name']} ({town['capacity']})" for town in t.data))
@dataclass(frozen=True)
class TownList(JsonDocument):
    pass


def lua_string(text: str) -> str:
    """Double-quoted Lua string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --- town planning -------------------------------------------------------------------------------------------------


class TownParams(NodeParams):
    max_towns: int = param(0, ge=0)
    """At most this many towns, the largest first (0 = from towns_per_100_km2 and the map area)."""
    towns_per_100_km2: float = param(3.8, ge=0)
    """Towns per 100 km² game area when max_towns = 0."""
    place_types: tuple[str, ...] = ("city", "town", "village")
    """OSM place values that can become towns."""
    min_population: int = param(300, ge=0)
    """Smaller places never."""
    town_without_population: bool = False
    """City/town without a population count with min_population."""
    min_distance_game_m: float = param(3500.0, ge=0, unit="game m")
    """Minimum distance to all towns placed already (centre to centre)."""
    cell_px: int = param(8, ge=1)
    """Check grid of the building ground (heightmap pixels)."""
    max_slope: float = param(0.15, gt=0)
    """Steepest mean slope in game (height / distance) within core_game_m."""
    core_game_m: float = param(150.0, gt=0, unit="game m")
    """Radius around the centre that must be buildable (dry, no glacier/rock)."""
    snap_radius_game_m: float = param(600.0, ge=0, unit="game m")
    """Move a place at most this far, else skip it."""
    pop_to_capacity: tuple[tuple[int, int], ...] = ((300, 45), (1000, 110), (5000, 210), (20000, 400),
                                                    (100000, 800), (1000000, 1400))
    """Population -> start capacity per area like the editor (logarithmic in between)."""
    capacity_classes: tuple[tuple[int, str], ...] = ((80, "hamlet"), (150, "village"), (270, "small town"),
                                                     (550, "town"), (1000, "city"), (1000000000, "metropolis"))
    """Editor size classes (upper capacity limit, name) - for the log."""
    start_scale: float = param(1.0, gt=0)
    """All start sizes multiplied by this (e.g. 0.5 = half, they grow in game)."""
    min_start_capacity: int = param(45, ge=0)
    """Never smaller than this (a hamlet)."""
    ignore_size: bool = False
    """Ignore the real town size: all towns start at min_start_capacity (start_scale unused); places are still
    chosen largest first."""
    com_cargo: tuple[str, ...] = ("vegetables", "fish", "meat")
    """Commercial cargo demand in turns (like the game's random maps)."""
    ind_cargo: tuple[str, ...] = ("planks", "bricks", "fuel")
    """Industrial cargo demand in turns."""
    flip_y: bool = False
    """North is -y in game."""
    preview_width: int = param(2000, ge=100)
    """Width of the preview picture in pixels."""


@dataclass(frozen=True)
class Place:
    name: str
    population: int
    kind: str
    u: float                                        # map fractions
    v: float


@dataclass(frozen=True)
class Town:
    place: Place
    x: float                                        # game metres from the centre, north = +y
    y: float
    shift_m: float                                  # moved this far to buildable land
    capacity: int                                   # start capacity per area (editor)


class Towns(NamedTuple):
    towns: TownList
    lua: TextDocument
    preview: Image


@node("towns.plan")
def plan(heights: Field, heightmap: Image, black_m: float, water: Mask, shore: Mask, glacier: Mask, rock: Mask,
         places: OsmPoints, biome_preview: Image, map_format: MapFormat, params: TownParams,
         context: NodeContext) -> Towns:
    """Towns at OSM places with a population, largest first, on buildable land (dry, no glacier or rock, gentle
    slope) at a minimum distance; start capacity from the population. `heights` = real heights as the game will see
    them (decoded heightmap), `heightmap` = its pixels (0 = real water core). Outputs the town list, the map editor's
    import file (towns only) and a preview."""
    p = params
    if not p.max_towns:
        max_towns = round(p.towns_per_100_km2 * map_format.game_width_km * map_format.game_height_km / 100)
        p = p.model_copy(update={"max_towns": max_towns})
    frame = heights.frame
    px_h, px_w = heights.values.shape
    nx, ny = math.ceil(px_w / p.cell_px), math.ceil(px_h / p.cell_px)

    def to_grid(a: NDArray[Any]) -> FloatMap:
        return resize_floats(a, nx, ny, PilImage.Resampling.BOX)

    game_height = to_grid((heights.values - black_m) * map_format.height_game_per_real)
    blocked = to_grid((heightmap.pixels == 0).astype("float32")) > 0       # core of game water
    for layer in (water, shore, glacier, rock):
        blocked = blocked | (to_grid(layer.values.astype(np.float32)) > _LAYER_LIMIT)
    grid = _BuildableGrid(p, game_height, blocked, context)
    chosen, skipped = _TownPlanner(p, px_w, px_h, context).plan(_places(places, p, frame, context), grid)
    lua = _import_file(chosen, p)
    data = [{"name": t.place.name, "x": round(t.x, 1), "y": round(t.y, 1), "capacity": t.capacity} for t in chosen]
    preview = _preview(biome_preview, chosen, skipped, p, (px_w - 1) * GAME_METERS_PER_PIXEL,
                       (px_h - 1) * GAME_METERS_PER_PIXEL)
    return Towns(TownList(data), TextDocument(lua), Image(np.asarray(preview)))


def population(tags: Mapping[str, str]) -> int | None:
    digits = re.sub(r"[^\d]", "", tags.get("population", "").split(";")[0])
    return int(digits) if digits else None


def _places(points: OsmPoints, p: TownParams, frame: MapFrame, context: NodeContext) -> list[Place]:
    """OSM places of the wanted kinds with a population, on the map, largest first."""
    projection = MapProjection(frame)
    found = []
    for e in points.points:
        tags = e.tags
        if tags.get("place") not in p.place_types:
            continue
        pop = population(tags)
        if pop is None:
            if not (p.town_without_population and tags.get("place") in ("city", "town")):
                continue
            pop = p.min_population
        if pop < p.min_population:
            continue
        u, v = projection.ring_to_uv([(e.lon, e.lat)])[0]
        if 0 <= u <= 1 and 0 <= v <= 1:
            found.append(Place(tags.get("name", "?"), pop, tags.get("place", ""), u, v))
    found.sort(key=lambda place: -place.population)
    context.log(f"places: {len(found)} with >= {p.min_population} inhabitants on the map")
    return found


class _BuildableGrid:
    """Coarse grid: buildable where no blocked cell lies within the core radius and the mean slope there is small
    enough; for every cell the nearest buildable cell."""

    def __init__(self, params: TownParams, game_height: FloatMap, blocked: BoolMap, context: NodeContext) -> None:
        self.cell_m = params.cell_px * GAME_METERS_PER_PIXEL
        gy, gx = np.gradient(game_height, self.cell_m)
        slope = np.hypot(gx, gy)
        core = max(1, round(params.core_game_m / self.cell_m))
        yy, xx = np.ogrid[-core:core + 1, -core:core + 1]
        disk = xx ** 2 + yy ** 2 <= core ** 2
        kernel = disk.astype(np.float32) / disk.sum()
        mean_slope = convolve(slope, kernel, mode="nearest")
        self.suitable = ~binary_dilation(blocked, structure=disk) & (mean_slope <= params.max_slope)
        context.log(f"buildable: {self.suitable.mean() * 100:.1f} % of the map")
        _, (self.near_y, self.near_x) = distance_transform_edt(~self.suitable, return_indices=True)


class _TownPlanner:
    """Takes the places from the largest down until max_towns; skips places without buildable land within the snap
    radius and places too close to a town chosen already."""

    def __init__(self, params: TownParams, width_px: int, height_px: int, context: NodeContext) -> None:
        self._params = params
        self._width_px = width_px
        self._height_px = height_px
        self._game_w = (width_px - 1) * GAME_METERS_PER_PIXEL
        self._game_h = (height_px - 1) * GAME_METERS_PER_PIXEL
        self._context = context

    def plan(self, places: Sequence[Place], grid: _BuildableGrid) -> tuple[list[Town], list[tuple[Place, str]]]:
        p = self._params
        ny, nx = grid.suitable.shape
        chosen: list[Town] = []
        skipped: list[tuple[Place, str]] = []
        for place in places:
            if len(chosen) >= p.max_towns:
                break
            cx = min(int(place.u * (self._width_px - 1) / p.cell_px), nx - 1)
            cy = min(int(place.v * (self._height_px - 1) / p.cell_px), ny - 1)
            sy, sx = grid.near_y[cy, cx], grid.near_x[cy, cx]
            shift = math.hypot(sx - cx, sy - cy) * grid.cell_m
            if not grid.suitable[sy, sx] or shift > p.snap_radius_game_m:
                skipped.append((place, "no building land"))
                continue
            x = ((sx + 0.5) * p.cell_px / (self._width_px - 1) - 0.5) * self._game_w     # cell centre
            y = (0.5 - (sy + 0.5) * p.cell_px / (self._height_px - 1)) * self._game_h
            if p.flip_y:
                y = -y
            close = [t for t in chosen if math.hypot(t.x - x, t.y - y) < p.min_distance_game_m]
            if close:
                skipped.append((place, f"too close to {close[0].place.name}"))
                continue
            capacity = p.min_start_capacity if p.ignore_size else max(
                p.min_start_capacity, round(self.target_capacity(place.population) * p.start_scale))
            chosen.append(Town(place, x, y, shift, capacity))
        self._log(chosen, skipped)
        return chosen, skipped

    def target_capacity(self, population: int) -> int:
        """Capacity per area, logarithmic between the support points pop_to_capacity."""
        pts = self._params.pop_to_capacity
        if population <= pts[0][0]:
            return pts[0][1]
        for (p0, c0), (p1, c1) in itertools.pairwise(pts):
            if population <= p1:
                t = (math.log(population) - math.log(p0)) / (math.log(p1) - math.log(p0))
                return round(c0 + (c1 - c0) * t)
        return pts[-1][1]

    def _log(self, chosen: Sequence[Town], skipped: Sequence[tuple[Place, str]]) -> None:
        log = self._context.log
        classes = self._params.capacity_classes
        log(f"{len(chosen)} towns (target {self._params.max_towns}):")
        for i, t in enumerate(chosen, 1):
            size = next(name for limit, name in classes if t.capacity < limit)
            log(f"  {i:3d} {t.place.name[:28]:<28} {t.place.population:9d}  {t.capacity:5d} {size:<11} "
                f"{t.shift_m:6.0f} m moved   {t.x:8.0f} / {t.y:8.0f}")
        if skipped:
            log(f"skipped ({len(skipped)}): " + ", ".join(f"{p.name} ({why})" for p, why in skipped[:20])
                + (" ..." if len(skipped) > 20 else ""))


def _import_file(towns: Sequence[Town], p: TownParams) -> str:
    """Import file of the map editor (towns/industries) with the towns only, cargo demand in turns."""
    com_cargo, ind_cargo = p.com_cargo, p.ind_cargo
    lines = ["function data()", "return {", "\tindustries = { },", "\ttowns = {"]
    for i, t in enumerate(towns):
        com = com_cargo[i % len(com_cargo)]
        ind = ind_cargo[(i + i // len(com_cargo)) % len(ind_cargo)]
        lines += [
            "\t\t{",
            "\t\t\tlandUse2CargoNeedsCategories = {",
            f'\t\t\t\tcom = {{ {{ "{com}", 1, }}, }},',
            f'\t\t\t\tind = {{ {{ "{ind}", 1, }}, }},',
            "\t\t\t},",
            f"\t\t\tname = {lua_string(t.place.name)},",
            f"\t\t\tposition = {{ x = {t.x:.1f}, y = {t.y:.1f}, }},",
            "\t\t\tsizeFactors = { " + ", ".join([f"{t.capacity / 100:g}"] * 3) + ", },",
            "\t\t},",
        ]
    lines += ["\t},", "}", "end", ""]
    return "\n".join(lines)


def _preview(biome_preview: Image, towns: Sequence[Town], skipped: Sequence[tuple[Place, str]], p: TownParams,
             game_w: float, game_h: float) -> PilImage.Image:
    """Biome preview with the towns (red, number = start capacity), half the minimum distance (white circle) and the
    skipped places (grey x)."""
    base = PilImage.fromarray(biome_preview.pixels).convert("RGB")
    pw = p.preview_width
    ph = round(pw * base.size[1] / base.size[0])
    img = base.resize((pw, ph), PilImage.Resampling.BILINEAR)
    draw = ImageDraw.Draw(img)
    font = preview_font()
    sx, sy = pw / game_w, ph / game_h

    def to_px(x: float, y: float) -> tuple[float, float]:
        return (x + game_w / 2) * sx, (game_h / 2 - (-y if p.flip_y else y)) * sy

    for place, _ in skipped:
        x, y = place.u * pw, place.v * ph
        draw.line([(x - 4, y - 4), (x + 4, y + 4)], fill=(120, 120, 120), width=2)
        draw.line([(x - 4, y + 4), (x + 4, y - 4)], fill=(120, 120, 120), width=2)
    for i, t in enumerate(towns, 1):
        x, y = to_px(t.x, t.y)
        r = 3 + 0.01 * t.capacity
        rd = p.min_distance_game_m / 2 * sx
        draw.ellipse([x - rd, y - rd, x + rd, y + rd], outline=(255, 255, 255), width=1)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(220, 0, 0), outline=(0, 0, 0), width=2)
        draw.text((x + r + 3, y - 8), f"{i} {t.place.name} ({t.capacity})", fill=(0, 0, 0), font=font,
                  stroke_width=2, stroke_fill=(255, 255, 255))
    return img


# --- settling ------------------------------------------------------------------------------------------------------


class SettleParams(NodeParams):
    town_radius_game_m: float = param(250.0, ge=0, unit="game m")
    """Base radius of a town."""
    town_radius_per_sqrt_capacity: float = param(20.0, ge=0)
    """+ this per square root of the start capacity."""
    town_radius_max_game_m: float = param(1000.0, ge=0, unit="game m")
    """Largest town radius."""
    town_max_slope: float = param(0.06, ge=0)
    """Steepest town plane (height / distance, game)."""
    radius_by_site_game_m: dict[str, float] = Setting(
        default_factory=lambda: {"factory": 170.0, "field": 200.0, "compact": 120.0})
    """Radius around industries by site kind (factory, field = with fields/pits, compact = in the mountains)."""
    blend_game_m: float = param(200.0, gt=0, unit="game m")
    """Width of the transition to the terrain outside."""
    edge_noise_game_m: float = param(80.0, ge=0, unit="game m")
    """Border shifted by up to this much (0 = round)."""
    edge_noise_wave_game_m: float = param(300.0, gt=0, unit="game m")
    """Structure size of the border noise."""
    strength: float = param(1.0, ge=0, le=1)
    """1 = fully onto the plane."""
    max_change_game_m: float = param(15.0, ge=0, unit="game m")
    """At most this much cut/fill."""
    seed: int = 5
    """Random seed of the border noise."""


@dataclass(frozen=True)
class _Site:
    """A place to even out; x, y in game metres from the map centre (y up)."""

    name: str
    x: float
    y: float
    radius_game_m: float
    plane: bool                            # tilted plane (town) instead of level (industry)


class Settled(NamedTuple):
    heights: Field
    preview: Image


@node("settle.terrain")
def settle(heights: Field, water: Mask, land_min_m: float, map_format: MapFormat, params: SettleParams,
           context: NodeContext, towns: TownList | None = None, industries: JsonDocument | None = None) -> Settled:
    """Evens out the terrain around towns (tilted plane, slope limited) and industries (level; fishing grounds and
    industries on water left out) before the water is shaped; `water` stays as it is. Overlapping sites are averaged by
    weight (no step between planes); the border is noisy and blends into the terrain."""
    sites = _sites(params, towns.data if towns else None,
                   industries.data.get("industries") if industries and isinstance(industries.data, dict) else
                   (industries.data if industries else None))
    settler = _Settler(params, map_format.height_game_per_real, land_min_m, context)
    dem, preview = settler.settle(heights.values, water.values, sites)
    if preview is None:
        preview = np.zeros((1, 1, 3), dtype=np.uint8)
    return Settled(heights.like(dem), Image(preview))


def _sites(p: SettleParams, towns: Iterable[Mapping[str, Any]] | None,
           industries: Iterable[Mapping[str, Any]] | None) -> list[_Site]:
    sites = []
    for town in towns or ():
        radius = p.town_radius_game_m + p.town_radius_per_sqrt_capacity * math.sqrt(max(town["capacity"], 0))
        sites.append(_Site(town["name"], town["x"], town["y"], min(radius, p.town_radius_max_game_m), True))
    for industry in industries or ():
        if industry.get("onWater") or industry["type"] == "fishing_grounds":
            continue
        radius = p.radius_by_site_game_m.get(str(industry.get("site")), p.radius_by_site_game_m["factory"])
        sites.append(_Site(industry["name"], industry["x"], industry["y"], radius, False))
    return sites


@dataclass
class _Contributions:
    """Weighted height changes of all sites per pixel index."""

    index: list[NDArray[np.int64]]
    weight: list[NDArray[np.float32]]
    change: list[NDArray[np.float32]]
    towns: int = 0
    industries: int = 0
    clipped: int = 0


class _Settler:
    def __init__(self, params: SettleParams, height_scale: float, land_min_m: float, context: NodeContext) -> None:
        self._params = params
        self._height_scale = height_scale
        self._land_min_m = land_min_m
        self._context = context

    def settle(self, dem: FloatMap, water: BoolMap,
               sites: Sequence[_Site]) -> tuple[FloatMap, NDArray[np.uint8] | None]:
        if not sites:
            return dem, None
        found = self._contributions(dem, water, sites)
        if not found.index:
            return dem, None
        scale, px = self._height_scale, GAME_METERS_PER_PIXEL
        u, delta = self._averaged(found)
        dem = np.array(dem, dtype=np.float32)                   # writable copy
        flat = dem.reshape(-1)
        flat[u] = np.maximum(flat[u] + delta, np.float32(self._land_min_m))
        game = delta * scale
        changed = np.abs(game) > 0.5
        self._context.log(f"{found.towns} towns (tilted plane), {found.industries} industries (level); changed > "
                          f"0.5 m: {changed.sum() * px * px / 1e4:.0f} ha game area, cut up to {-game.min():.1f} m, "
                          f"fill up to {game.max():.1f} m (game); {found.clipped} sites at the change limit")
        return dem, self._preview(dem, u, game)

    @staticmethod
    def _averaged(found: _Contributions) -> tuple[NDArray[np.int64], NDArray[np.float32]]:
        """Pixel indices and their change: weighted mean of all sites, scaled by the largest weight."""
        idx = np.concatenate(found.index)
        wv, dv = np.concatenate(found.weight), np.concatenate(found.change)
        u, inv = np.unique(idx, return_inverse=True)
        sw = np.bincount(inv, weights=wv)
        swd = np.bincount(inv, weights=wv * dv)
        mw = np.zeros(len(u))
        np.maximum.at(mw, inv, wv)
        return u, (swd / sw * mw).astype(np.float32)

    def _contributions(self, dem: FloatMap, water: BoolMap, sites: Sequence[_Site]) -> _Contributions:
        p, scale = self._params, self._height_scale
        h_, w_ = dem.shape
        px = GAME_METERS_PER_PIXEL
        max_change = p.max_change_game_m / scale                  # real metres
        max_grad = p.town_max_slope * px / scale                  # real metres per pixel
        step = max(1, round(p.edge_noise_wave_game_m / px / 4))   # smooth noise, coarse, sampled per site
        rng = np.random.default_rng(p.seed)
        noise = gaussian_filter(rng.standard_normal((h_ // step + 2, w_ // step + 2)).astype(np.float32),
                                sigma=p.edge_noise_wave_game_m / px / step / 2)
        noise /= max(float(noise.std()), 1e-6)
        blend = max(p.blend_game_m / px, 1.0)
        edge = p.edge_noise_game_m / px
        found = _Contributions([], [], [])
        for s in sites:
            ci = s.x / px + (w_ - 1) / 2
            ri = (h_ - 1) / 2 - s.y / px
            rad = s.radius_game_m / px
            ext = rad + blend + edge
            r0, r1 = max(0, int(ri - ext)), min(h_, int(ri + ext) + 2)
            c0, c1 = max(0, int(ci - ext)), min(w_, int(ci + ext) + 2)
            if r0 >= r1 or c0 >= c1:
                continue
            yy_, xx_ = np.mgrid[r0:r1, c0:c1].astype(np.float32)
            dy, dx = yy_ - ri, xx_ - ci
            dist = np.hypot(dx, dy)
            sub = dem[r0:r1, c0:c1]
            wat = water[r0:r1, c0:c1]
            core = (dist <= rad) & ~wat
            if core.sum() < _MIN_CORE_PIXELS:
                continue
            if s.plane:
                target = self._plane(sub, core, dx, dy, max_grad)
                found.towns += 1
            else:
                target = float(np.median(sub[core]))
                found.industries += 1
            d = target - sub
            found.clipped += bool((np.abs(d[core]) > max_change).any())
            d = np.clip(d, -max_change, max_change)
            de = dist - map_coordinates(noise, [yy_ / step, xx_ / step], order=1) * edge
            w = np.clip((de - rad) / blend, 0, 1)
            w = (0.5 + 0.5 * np.cos(np.pi * w)) * p.strength
            w[wat] = 0
            m = w > 1e-3
            found.index.append(yy_[m].astype(np.int64) * w_ + xx_[m].astype(np.int64))
            found.weight.append(w[m].astype(np.float32))
            found.change.append(d[m].astype(np.float32))
        return found

    @staticmethod
    def _plane(sub: FloatMap, core: BoolMap, dx: FloatMap, dy: FloatMap, max_grad: float) -> Any:
        """Best-fit plane through the core, its gradient limited to `max_grad`, level = median."""
        k = max(1, int(math.sqrt(core.sum() / _PLANE_SAMPLE_PIXELS)))
        sel = core[::k, ::k]
        z, ax, ay = sub[::k, ::k][sel], dx[::k, ::k][sel], dy[::k, ::k][sel]
        _, gx_, gy_ = np.linalg.lstsq(np.stack([np.ones_like(ax), ax, ay], 1), z, rcond=None)[0]
        g = math.hypot(gx_, gy_)
        if g > max_grad:
            gx_, gy_ = gx_ * max_grad / g, gy_ * max_grad / g
        a0 = float(np.median(sub[core] - gx_ * dx[core] - gy_ * dy[core]))
        return a0 + gx_ * dx + gy_ * dy

    @staticmethod
    def _preview(dem: FloatMap, changed: NDArray[np.int64], game: NDArray[np.float32]) -> NDArray[np.uint8]:
        """Change over grey terrain (blue = cut, red = fill)."""
        h_, w_ = dem.shape
        f = max(1, math.ceil(w_ / _PREVIEW_MAX_WIDTH))
        ph, pw = math.ceil(h_ / f), math.ceil(w_ / f)
        cell_ = (changed // w_ // f) * pw + (changed % w_ // f)
        acc = np.bincount(cell_, weights=game, minlength=ph * pw).reshape(ph, pw)
        cnt = np.bincount(cell_, minlength=ph * pw).reshape(ph, pw)
        mean = np.where(cnt > 0, acc / np.maximum(cnt, 1), 0)
        terrain = dem[::f, ::f][:ph, :pw]
        grey = (60 + 160 * (terrain - terrain.min()) / max(float(np.ptp(terrain)), 1)).astype(np.float32)
        rgb = np.repeat(grey[..., None], 3, axis=2)
        a = np.clip(np.abs(mean) / _PREVIEW_FULL_COLOR_M, 0, 1)[..., None]
        col = np.where((mean < 0)[..., None], np.array([40, 90, 255], np.float32), np.array([255, 60, 40], np.float32))
        tinted = rgb * (1 - a) + col * a
        return np.asarray(tinted.astype(np.uint8))
