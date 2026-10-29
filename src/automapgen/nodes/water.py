"""Water: classify WorldCover water, add the planned rivers, level the surfaces, shape banks and real water; the water
and shore import layers.

Widths with `_game_m` are game metres (a fixed number of pixels whatever the map scale); `_m` / `_ha` are real metres /
hectares (detection on the real data). Work grid = landcover grid; the result is applied to the heightmap grid.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
from numpy.typing import NDArray
from PIL import Image as PilImage
from PIL import ImageDraw
from pydantic import BaseModel, ConfigDict
from scipy.ndimage import (
    binary_dilation,
    binary_erosion,
    distance_transform_edt,
    find_objects,
    gaussian_filter,
    label,
    maximum_filter,
)

from automapgen.graph import NodeContext, NodeParams, data_type, node, param
from automapgen.nodes.maps import GAME_METERS_PER_PIXEL, MapFormat, MapFrame
from automapgen.nodes.rasters import ClassMap, Field, Mask, resize_floats
from automapgen.nodes.rivers import RiverPlan, real_water_until

WATER_CLASS = 80                    # ESA WorldCover "permanent water bodies"
_CONNECTED = np.ones((3, 3), dtype=bool)
_LAKE, _RIVER = 1, 2
_LAKE_LEVEL_RANGE = (5, 95)        # percentiles of the height spread that decides lake vs. river
_SHORE_NOISE_SEED = 4711

type BoolMap = NDArray[np.bool_]
type FloatMap = NDArray[np.float32]
type Log = Callable[[str], None]


@dataclass(frozen=True)
class BedSegment:
    """Painted riverbed piece from the river plan: map fractions (u, v) and width in game metres."""

    start: tuple[float, float]
    end: tuple[float, float]
    width_game_m: float


class _SegmentsCodec:
    suffix = ".json"

    def save(self, value: BedSegments, path: Path) -> None:
        path.write_text(json.dumps([[s.start, s.end, s.width_game_m] for s in value.segments]), encoding="utf-8")

    def load(self, path: Path) -> BedSegments:
        return BedSegments(tuple(BedSegment(tuple(a), tuple(b), w)
                                 for a, b, w in json.loads(path.read_text(encoding="utf-8"))))


@data_type("bed_segments", "Painted riverbed pieces (map fractions, width in game metres).", codec=_SegmentsCodec(),
           preview=lambda s: f"{len(s.segments)} painted riverbed pieces")
@dataclass(frozen=True)
class BedSegments:
    segments: tuple[BedSegment, ...]


class _Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class BodyParams(_Settings):
    min_area_ha: float = 0.25
    """Smaller areas are discarded (misclassified, tiny ponds on slopes)."""
    lake_max_range_m: float = 8
    """Height spread (5th-95th percentile) up to which an area is levelled (lake)."""
    lake_level_percentile: float = 25
    """Lake level = this percentile of the heights in the area."""
    river_min_elongation: float = 5
    """Length/width from which a too steep area counts as river."""
    keep_area_ha: float = 10
    """Larger too steep areas are never discarded but smoothed like rivers."""
    river_smooth_game_m: float = 100
    """Smoothing of the river surface along its course."""
    isolated_real_max_m: float = 15
    """Lakes without connection to a real river: real only up to this height above the water line."""


class BankParams(_Settings):
    border_game_m: float = 25
    """Border around the water (levelled, shore texture)."""
    smooth_by_slope: bool = True
    """Bank width from the terrain slope (else smooth_factor x depth)."""
    smooth_factor: float = 4
    """Without bank slope: bank width = levelling depth x this factor."""
    max_game_m: float = 150
    """Bank width at most (painted water)."""
    max_real_game_m: float = 250
    """Bank width at most at real water."""
    sigma_game_m: float = 15
    """Gaussian radius per smoothing pass."""
    passes: int = 30
    """Number of Gaussian smoothing passes in the bank zone."""
    bank_slope: tuple[float, float] = (0.05, 0.5)
    """Target bank slope: flattest .. steepest."""
    bank_slope_factor: float = 1
    """Target bank slope = terrain slope x this factor (limited to bank_slope)."""
    slope_smooth_game_m: float = 100
    """Terrain slope averaged over this radius."""
    flat_slope: float = 0.08
    """Terrain flatter than this slope (height / distance) gets extra passes."""
    passes_flat: int = 30
    """Extra smoothing passes in flat terrain."""


class RealWaterParams(_Settings):
    edge_smooth_game_m: float = 10
    """Smooth the edge of real water at full resolution (0 = off)."""
    bed_ramp_game_m: float = 40
    """Bed falls from the shore to the bottom over this width (0 = step)."""
    bed_smooth_game_m: float = 6
    """Blur of the bed ramp (0 = off)."""
    shore_width_game_m: float = 60
    """Width of the shore strip next to real water that is smoothed."""
    shore_smooth_game_m: float = 12
    """Smoothing radius of that shore strip (0 = off)."""


class ShapeParams(NodeParams):
    real_water_until_m: float | None = param(None, unit="m")
    """Real water (game water) only up to this height above sea level (the region's "real water up to"); None = 120 m
    above the lowest land. Higher = more real water, but real water is cut down to the water line (deep trenches)."""
    water_sink_m: float = param(40.0, ge=0, unit="m")
    """Core of real water this much below the water line (black of the heightmap)."""
    shore_class: int = param(200, ge=0, le=255)
    """Own landcover class of the shore (not a WorldCover code)."""
    report_min_ha: float = param(20.0, ge=0, unit="ha")
    """Lakes from this size are listed in the log."""
    bodies: BodyParams = BodyParams()
    """Which water areas become lakes, rivers or are discarded."""
    banks: BankParams = BankParams()
    """Border and banks around the water."""
    real: RealWaterParams = RealWaterParams()
    """Real water (game water): soft edge, bed ramp, shore strip."""


class WaterShape(NamedTuple):
    heights: Field
    landcover: ClassMap
    core: Mask
    border: Mask
    real: Mask
    beds: BedSegments


@node("water.shape")
def shape(heights: Field, landcover: ClassMap, land_min_m: float, map_format: MapFormat, params: ShapeParams,
          context: NodeContext, rivers: RiverPlan | None = None) -> WaterShape:
    """Levels lakes, smooths rivers, adds the real river bands of the river plan, shapes banks and real water.

    Outputs: terrain, landcover with shore and river classes, water core / levelled border / real water on the
    heightmap grid, and the painted riverbed pieces of the plan."""
    frame = heights.frame
    levels = _Levels(real_water_until(params.real_water_until_m, land_min_m) - land_min_m, params.water_sink_m)
    shaper = _WaterShaper(params, levels, frame, map_format.height_game_per_real, context.log)
    shaped = shaper.shape(heights.values, landcover.values, land_min_m, rivers.data if rivers is not None else None)
    empty = np.zeros((frame.height_px, frame.width_px), dtype=bool)

    def mask(values: BoolMap | None) -> Mask:
        return Mask(empty if values is None else values, frame)

    return WaterShape(heights.like(shaped.heights), landcover.like(shaped.landcover), mask(shaped.core),
                      mask(shaped.border), mask(shaped.real), BedSegments(tuple(shaped.bed_segments)))


@dataclass(frozen=True)
class _Levels:
    real_water_max_m: float            # real water up to this height above the water line
    water_sink_m: float                # core of real water this much below the water line (black)


@dataclass(frozen=True)
class _Shaped:
    heights: FloatMap
    landcover: NDArray[np.uint8]
    core: BoolMap | None
    border: BoolMap | None
    real: BoolMap | None
    bed_segments: Sequence[BedSegment]


@dataclass(frozen=True)
class _WorkGrid:
    cols: int
    rows: int
    px_x: float
    px_y: float

    @property
    def px(self) -> float:
        return (self.px_x + self.px_y) / 2

    @property
    def pixel_ha(self) -> float:
        return self.px_x * self.px_y / 1e4


@dataclass(frozen=True)
class _Bodies:
    labels: NDArray[np.int32]
    count: int
    area_ha: NDArray[Any]
    kind: NDArray[np.uint8]            # 0 = discard, 1 = lake, 2 = river / large area
    level: NDArray[np.float32]


@dataclass(frozen=True)
class _RiverBands:
    mask: BoolMap
    real: BoolMap
    crossed: BoolMap                   # lake pixels under a real band (the lake keeps no level of its own)
    painted_crossing: BoolMap          # under painted segments of non-navigable rivers ("navigable" plans only)
    level: FloatMap
    segments: list[BedSegment]


@dataclass
class _Surfaces:
    keep: BoolMap
    lake: BoolMap
    river: BoolMap
    real: BoolMap
    const: BoolMap                     # surface without gradient -> transferred without interpolation
    sink: FloatMap
    core_level: FloatMap


class _BodyClassifier:
    """Connected WorldCover water areas -> lake (one level), river / large area (smoothed) or discarded."""

    def __init__(self, params: BodyParams) -> None:
        self._params = params

    def classify(self, water: BoolMap, dem_lc: FloatMap, grid: _WorkGrid) -> _Bodies:
        p = self._params
        labels, count = label(water, structure=_CONNECTED)
        area_ha = np.bincount(labels.ravel(), minlength=count + 1) * grid.px_x * grid.px_y / 1e4
        inner = distance_transform_edt(water, sampling=(grid.px_y, grid.px_x))
        kind = np.zeros(count + 1, dtype=np.uint8)
        level = np.zeros(count + 1, dtype=np.float32)
        for i, sl in enumerate(find_objects(labels), start=1):
            if area_ha[i] < p.min_area_ha:
                continue
            comp = labels[sl] == i
            h = dem_lc[sl][comp]
            lo, hi = np.percentile(h, _LAKE_LEVEL_RANGE)
            if hi - lo <= p.lake_max_range_m:
                kind[i] = _LAKE
                level[i] = np.percentile(h, p.lake_level_percentile)
            else:
                width = 2 * float(inner[sl][comp].max())
                length = area_ha[i] * 1e4 / width
                if length / width >= p.river_min_elongation or area_ha[i] >= p.keep_area_ha:
                    kind[i] = _RIVER
        return _Bodies(labels, count, area_ha, kind, level)


class _RiverBandPainter:
    """Rivers of the river plan: real sections become a band of water (level = water line), painted sections are
    kept as riverbed segments; in a "navigable" plan the painted segments of non-navigable rivers are also drawn as a
    mask (`painted_crossing`)."""

    def __init__(self, frame: MapFrame, water_line: float, real_water_max_m: float, log: Log) -> None:
        self._frame = frame
        self._water_line = water_line
        self._real_water_max_m = real_water_max_m
        self._log = log

    def paint(self, plan: Mapping[str, Any], grid: _WorkGrid, lake: BoolMap) -> _RiverBands:
        self._check(plan)
        level_img = PilImage.new("F", (grid.cols, grid.rows), 0.0)
        mask_img = PilImage.new("L", (grid.cols, grid.rows), 0)
        real_img = PilImage.new("L", (grid.cols, grid.rows), 0)
        painted_img = PilImage.new("L", (grid.cols, grid.rows), 0)
        d_l, d_m, d_r = ImageDraw.Draw(level_img), ImageDraw.Draw(mask_img), ImageDraw.Draw(real_img)
        d_p = ImageDraw.Draw(painted_img)
        navigable_plan = plan.get("real_water_rivers", "all") == "navigable"
        segments = []
        for river in plan["rivers"]:
            pts = [(u * (grid.cols - 1), v * (grid.rows - 1)) for u, v in river["points"]]
            for k in range(len(pts) - 1):
                w_game = (river["width_game_m"][k] + river["width_game_m"][k + 1]) / 2
                wpx = max(1, round(self._frame.game_to_real_m(w_game) / grid.px))
                seg = [pts[k], pts[k + 1]]
                if not (river["real"][k] and river["real"][k + 1]):
                    segments.append(BedSegment(tuple(river["points"][k]), tuple(river["points"][k + 1]), w_game))
                    if navigable_plan and not (river["navigable"][k] and river["navigable"][k + 1]):
                        d_p.line(seg, fill=255, width=wpx, joint="curve")
                    continue
                d_l.line(seg, fill=float(self._water_line), width=wpx, joint="curve")
                d_m.line(seg, fill=255, width=wpx, joint="curve")
                d_r.line(seg, fill=255, width=wpx, joint="curve")
        mask = (np.asarray(mask_img) > 0) & ~lake     # lakes keep their shape and level, unless the band is real
        real = np.asarray(real_img) > 0
        return _RiverBands(mask, mask & real, lake & real, np.asarray(painted_img) > 0,
                           np.asarray(level_img, dtype=np.float32), segments)

    def _check(self, plan: Mapping[str, Any]) -> None:
        used_until = plan["ref_m"] + plan["real_water_max_m"]
        until = self._water_line + self._real_water_max_m
        if abs(plan["ref_m"] - self._water_line) > 0.5 or abs(used_until - until) > 0.5:
            self._log(f"warning: river plan made for reference height {plan['ref_m']:g} m / real water up to "
                      f"{used_until:g} m, now {self._water_line:g} m / up to {until:g} m - rivers and lakes do not "
                      f"match")


class _WaterShaper:
    """Levels water and border, smooths the banks, lowers real water, at work and full resolution."""

    def __init__(self, params: ShapeParams, levels: _Levels, frame: MapFrame, height_game_per_real: float,
                 log: Log) -> None:
        self._params = params
        self._levels = levels
        self._frame = frame
        self._height_game_per_real = height_game_per_real
        self._log = log

    def shape(self, dem: FloatMap, landcover: NDArray[np.uint8], land_min_m: float,
              river_plan: Mapping[str, Any] | None) -> _Shaped:
        p, frame = self._params, self._frame
        landcover = landcover.copy()
        rows, cols = landcover.shape
        px_x, px_y = frame.meters_per_pixel(cols, rows)
        grid = _WorkGrid(cols, rows, px_x, px_y)
        water_line = land_min_m           # border of real water; core water_sink_m lower (black)
        dem_lc = resize_floats(dem, cols, rows, PilImage.Resampling.BOX)
        water_lc = landcover == WATER_CLASS
        bodies = _BodyClassifier(p.bodies).classify(water_lc, dem_lc, grid)
        kinds = bodies.kind[bodies.labels]
        lake = water_lc & (kinds == _LAKE)
        river = water_lc & (kinds == _RIVER)
        del kinds
        keep = lake | river
        discard = water_lc & ~keep

        bands = None
        if river_plan is not None:
            bands = _RiverBandPainter(frame, water_line, self._levels.real_water_max_m, self._log).paint(
                river_plan, grid, lake)
            saved = (discard & bands.mask).sum()
            discard &= ~bands.mask                     # discarded WorldCover pieces in the band stay water
            landcover[bands.mask] = WATER_CLASS
            self._log(f"rivers from the plan: real water {bands.mask.sum() * grid.pixel_ha:.0f} ha band, "
                      f"{len(bands.segments)} painted riverbed pieces; {saved * grid.pixel_ha:.1f} ha WorldCover "
                      f"pieces saved, {len(river_plan['weirs'])} weirs")

        if discard.any():                              # discarded areas take the class of the nearest dry pixel
            near = distance_transform_edt(water_lc, return_distances=False, return_indices=True)
            landcover[discard] = landcover[near[0][discard], near[1][discard]]
            del near

        surfaces = self._surfaces(bodies, dem_lc, grid, water_line, keep, lake, river, bands)
        self._report(bodies, surfaces.real, water_line, bands is not None)
        self._log_bodies(bodies, surfaces, grid, water_line, dem_lc)
        segments = bands.segments if bands is not None else []
        if not surfaces.keep.any():
            return _Shaped(dem, landcover, None, None, None, segments)
        return self._shape_terrain(dem, dem_lc, landcover, grid, water_line, surfaces, segments)

    def _surfaces(self, bodies: _Bodies, dem_lc: FloatMap, grid: _WorkGrid, water_line: float, keep: BoolMap,
                  lake: BoolMap, river: BoolMap, bands: _RiverBands | None) -> _Surfaces:
        """Water level: a lake at one height, a river smoothed along its course (flat across); real water near the
        lowest point."""
        p = self._params.bodies
        real_water_max_m = self._levels.real_water_max_m
        surface = np.zeros_like(dem_lc)
        surface[lake] = bodies.level[bodies.labels[lake]]
        if river.any():
            m = river.astype(np.float32)
            river_smooth_m = self._frame.game_to_real_m(p.river_smooth_game_m)
            sigma = (river_smooth_m / grid.px_y, river_smooth_m / grid.px_x)
            smooth = gaussian_filter(dem_lc * m, sigma) / np.maximum(gaussian_filter(m, sigma), 1e-6)
            surface[river] = smooth[river]
            del m, smooth
        real = keep & (surface - water_line < real_water_max_m)
        if bands is not None:
            real = self._real_with_rivers(surface, real, keep, bands, bodies.labels, grid, water_line)
            keep = keep | bands.mask
            river = river | bands.mask
            lake = lake & ~bands.mask
        surface[real] = water_line
        # Real water: core water_sink_m below the border; painted water at the height of the border
        sink = np.where(real, self._levels.water_sink_m, 0).astype(np.float32)
        return _Surfaces(keep, lake, river, real, lake | real, sink, surface - sink)

    def _real_with_rivers(self, surface: FloatMap, real: BoolMap, keep: BoolMap, bands: _RiverBands,
                          body_labels: NDArray[np.int32], grid: _WorkGrid, water_line: float) -> BoolMap:
        """River band: level and real/painted from the plan. A lake crossed by a real band is real (a flat canal
        just above the real water limit would else stay painted inside the real river). Other water is real only when
        connected to a real river or near the water line - else painted at its own height (no craters around ponds).
        A "navigable" plan keeps water crossed by a painted non-navigable river painted when that river crosses more
        of its WorldCover area than real bands do: a river under or beside a canal stays painted water at its width,
        while the canal's own water at a brook mouth stays real."""
        p = self._params
        surface[bands.mask] = bands.level[bands.mask]
        border_m = self._frame.game_to_real_m(p.banks.border_game_m)
        rest_lab, n_rest = label(keep & ~bands.mask, structure=_CONNECTED)
        touch = binary_dilation(bands.real, iterations=max(1, round(border_m / grid.px)))
        ok_ids = np.zeros(n_rest + 1, bool)
        ok_ids[np.unique(rest_lab[touch & (rest_lab > 0)])] = True
        low = np.full(n_rest + 1, np.inf)
        np.minimum.at(low, rest_lab.ravel(), (surface - water_line).ravel())
        ok_ids |= low <= p.bodies.isolated_real_max_m
        ok_ids[0] = False
        crossed_ids = np.zeros(n_rest + 1, bool)
        crossed_ids[np.unique(rest_lab[bands.crossed])] = True
        crossed_ids[0] = False
        painted_ids = np.zeros(n_rest + 1, bool)
        count = body_labels.max() + 1
        by_band = np.bincount(body_labels[bands.real | bands.crossed], minlength=count)
        by_painted = np.bincount(body_labels[bands.painted_crossing], minlength=count)
        river_water = (by_painted > by_band)[body_labels]
        painted_ids[np.unique(rest_lab[bands.painted_crossing & river_water])] = True
        painted_ids[0] = False
        painted_ids &= ~crossed_ids
        if bands.painted_crossing.any():
            self._log(f"water areas crossed by a painted non-navigable river: {int(painted_ids.sum())} kept painted")
        n_painted = int((~ok_ids[1:] & (np.bincount(
            rest_lab[real & ~bands.mask].ravel(), minlength=n_rest + 1)[1:] > 0)).sum())
        self._log(f"water areas without connection to a real river and more than {p.bodies.isolated_real_max_m:g} m "
                  f"above the water line: {n_painted} painted instead of real")
        ok_ids &= ~painted_ids
        return np.asarray((real & ~bands.mask & ok_ids[rest_lab]) | crossed_ids[rest_lab] | bands.real)

    def _report(self, bodies: _Bodies, real: BoolMap, water_line: float, with_rivers: bool) -> None:
        """Lakes from report_min_ha with level, area and whether they are real (lowered) or painted."""
        p = self._params
        ids = [i for i in range(1, bodies.count + 1)
               if bodies.kind[i] == _LAKE and bodies.area_ha[i] >= p.report_min_ha]
        if not ids:
            return
        real_cnt = np.bincount(bodies.labels[real], minlength=bodies.count + 1)
        real_max, isolated_max = self._levels.real_water_max_m, p.bodies.isolated_real_max_m
        until = water_line + real_max
        scale = self._height_game_per_real
        self._log(f"lakes from {p.report_min_ha:g} ha (real water up to {until:g} m a.s.l.; without connection to a "
                  f"real river only up to {water_line + isolated_max:g} m):")
        for i in sorted(ids, key=lambda i: -bodies.area_ha[i]):
            above = float(bodies.level[i]) - water_line
            if real_cnt[i] > 0:
                what = f"real    - lowered {above:.0f} m ({above * scale:.0f} m in game)"
            elif above >= real_max:
                what = f"painted - level above {until:g} m"
            elif with_rivers:
                what = (f"painted - not connected to a real river ({above:.0f} m above the water line, allowed "
                        f"{isolated_max:g} m)")
            else:
                what = "painted"
            self._log(f"  lake {i:<4} {bodies.level[i]:6.0f} m {bodies.area_ha[i]:8.0f} ha  {what}")

    def _log_bodies(self, bodies: _Bodies, s: _Surfaces, grid: _WorkGrid, water_line: float,
                    dem_lc: FloatMap) -> None:
        self._log(f"water areas (land from {water_line:.1f} m):")
        for k, name in ((_LAKE, "lakes levelled"), (_RIVER, "rivers / large areas smoothed"), (0, "discarded")):
            sel = bodies.kind[1:] == k
            self._log(f"  {name:<34} {sel.sum():6d} areas {bodies.area_ha[1:][sel].sum():9.1f} ha")
        self._log(f"  of which real water (level < {self._levels.real_water_max_m:g} m above {water_line:g} m): "
                  f"{s.real.sum() * grid.pixel_ha:.1f} ha")

    def _shape_terrain(self, dem: FloatMap, dem_lc: FloatMap, landcover: NDArray[np.uint8], grid: _WorkGrid,
                       water_line: float, s: _Surfaces, segments: list[BedSegment]) -> _Shaped:
        banks = _BankShaper(self._params.banks, self._frame, self._log).shape(dem_lc, grid, s)
        landcover[banks.border] = self._params.shore_class   # forest, meadow etc. give way to the shore
        full = _FullResolutionWater(self._params, self._frame).apply(dem, dem_lc, s, banks)
        heights = _RealWaterBed(self._params.real, self._levels.water_sink_m, self._log).lower(
            full.heights, full.core, full.real, water_line)
        return _Shaped(heights, landcover, full.core, full.border, full.real, segments)


@dataclass(frozen=True)
class _Banks:
    border: BoolMap
    flat: BoolMap
    zone: BoolMap
    t: FloatMap                        # 0 at the water .. 1 at the outer edge of the bank
    relaxed: FloatMap                  # smoothed terrain
    core_ext: FloatMap                 # water level continued outwards
    border_ext: FloatMap
    const_ext: BoolMap


class _BankShaper:
    """Border = level of the nearest water; bank zone: ramp, then Gaussian passes only inside the zone (water, border
    and terrain outside stay fixed). Bank width = levelling depth / target slope (or factor x depth)."""

    def __init__(self, params: BankParams, frame: MapFrame, log: Log) -> None:
        self._params = params
        self._frame = frame
        self._log = log

    def shape(self, dem_lc: FloatMap, grid: _WorkGrid, s: _Surfaces) -> _Banks:
        p, to_real = self._params, self._frame.game_to_real_m
        border_m = to_real(p.border_game_m)
        dist, near = distance_transform_edt(~s.keep, sampling=(grid.px_y, grid.px_x), return_indices=True)
        core_ext = s.core_level[near[0], near[1]]
        const_ext = s.const[near[0], near[1]]
        border_ext = core_ext + s.sink[near[0], near[1]]
        real_ext = s.real[near[0], near[1]]           # nearest water real? (cap of the bank width)
        del near
        border = (dist > 0) & (dist <= border_m)
        flat = s.keep | border
        target = np.where(s.keep, core_ext, border_ext)
        del dist

        rad = max(1, round(border_m / grid.px))       # local levelling depth around each pixel
        depth = maximum_filter(np.where(flat, np.abs(dem_lc - target), 0), size=2 * rad + 1)
        dist2, near = distance_transform_edt(~flat, sampling=(grid.px_y, grid.px_x), return_indices=True)
        slope_lc = None
        if p.smooth_by_slope:
            gy_, gx_ = np.gradient(gaussian_filter(dem_lc, to_real(p.slope_smooth_game_m) / grid.px / 2),
                                   grid.px_y, grid.px_x)
            slope_lc = np.hypot(gx_, gy_)
            del gy_, gx_
            bank = np.clip(slope_lc * p.bank_slope_factor, *p.bank_slope)
            cap = np.where(real_ext[near[0], near[1]], to_real(p.max_real_game_m), to_real(p.max_game_m))
            width = np.minimum(depth[near[0], near[1]] / bank, cap)
            del bank, cap
        else:
            width = np.minimum(p.smooth_factor * depth[near[0], near[1]], to_real(p.max_game_m))
        target_near = target[near[0], near[1]]
        del near, depth, real_ext
        zone = ~flat & (dist2 < width)
        t = np.clip(dist2 / np.maximum(width, 1e-6), 0.0, 1.0).astype(np.float32)
        del dist2

        relaxed = dem_lc.copy()
        relaxed[flat] = target[flat]
        relaxed[zone] = (target_near + (dem_lc - target_near) * (t * t * (3 - 2 * t)))[zone]
        del target_near
        if zone.any():
            self._relax(relaxed, zone, slope_lc, grid)
        widest = width[zone].max() if zone.any() else 0
        self._log(f"border: {border.sum() * grid.pixel_ha:.1f} ha, bank zone: {zone.sum() * grid.pixel_ha:.1f} ha, "
                  f"bank width max. {widest:.0f} m")
        return _Banks(border, flat, zone, t, relaxed, core_ext, border_ext, const_ext)

    def _relax(self, relaxed: FloatMap, zone: BoolMap, slope_lc: FloatMap | None, grid: _WorkGrid) -> None:
        """Gaussian passes inside the zone (in place), more in flat terrain."""
        p = self._params
        sigma = self._frame.game_to_real_m(p.sigma_game_m) / grid.px
        ys, xs = np.nonzero(zone)
        pad = int(4 * sigma) + 2
        win = (slice(max(ys.min() - pad, 0), ys.max() + pad + 1), slice(max(xs.min() - pad, 0), xs.max() + pad + 1))
        del ys, xs
        part, zpart = relaxed[win], zone[win]
        for _ in range(p.passes):
            part[zpart] = gaussian_filter(part, sigma)[zpart]
        if slope_lc is not None and p.passes_flat > 0:
            fpart = zpart & (slope_lc[win] < p.flat_slope)
            for _ in range(p.passes_flat):
                part[fpart] = gaussian_filter(part, sigma)[fpart]


@dataclass(frozen=True)
class _FullWater:
    heights: FloatMap
    core: BoolMap
    border: BoolMap
    real: BoolMap


class _FullResolutionWater:
    """Transfers water, border and bank zone to the heightmap grid."""

    def __init__(self, params: ShapeParams, frame: MapFrame) -> None:
        self._params = params
        self._frame = frame

    def apply(self, dem: FloatMap, dem_lc: FloatMap, s: _Surfaces, banks: _Banks) -> _FullWater:
        w, h = self._frame.width_px, self._frame.height_px
        nearest, bilinear = PilImage.Resampling.NEAREST, PilImage.Resampling.BILINEAR

        def full(a: NDArray[Any], method: PilImage.Resampling) -> FloatMap:
            return resize_floats(a, w, h, method)

        core = full(s.keep, nearest) > 0.5                    # same pixels as the water mask
        real = core & (full(s.real, nearest) > 0.5)           # core below game water
        edge = self._params.real.edge_smooth_game_m
        if edge > 0 and real.any():
            # real water with a smooth edge (instead of the landcover grid staircase); painted water keeps the grid
            real_soft = gaussian_filter(full(s.real, bilinear), edge / GAME_METERS_PER_PIXEL) > 0.5
            core = (core & ~real) | real_soft
            real = real_soft
            del real_soft
        border_m = self._frame.game_to_real_m(self._params.banks.border_game_m)
        mpp_x, mpp_y = self._frame.meters_per_pixel()
        r = round(border_m / max(mpp_x, mpp_y))
        flat = core
        if r > 0:
            yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
            disk = (yy * mpp_y) ** 2 + (xx * mpp_x) ** 2 <= border_m ** 2
            flat = binary_dilation(core, structure=disk)
        border = flat & ~core

        # bank zone: smoothed height, terrain details faded back in towards the outer edge
        zone = (full(banks.zone | banks.flat, nearest) > 0.5) & ~flat
        new = full(banks.relaxed, bilinear)
        new += (dem - full(dem_lc, bilinear)) * full(banks.t, bilinear)
        heights = np.where(zone, new, dem)
        del new, zone

        # levelled area: constant levels without interpolation (else neighbouring levels mix), rivers interpolated
        const = full(banks.const_ext, nearest) > 0.5
        for ext, where in ((banks.border_ext, border), (banks.core_ext, core)):
            level = full(ext, bilinear)
            level[const] = full(ext, nearest)[const]
            heights[where] = level[where]
            del level
        return _FullWater(heights, core, border, real)


class _RealWaterBed:
    """Real water softer: the bed falls gradually from the shore to the bottom, the shore strip is smoothed, never
    below the water line (no overflow)."""

    def __init__(self, params: RealWaterParams, water_sink_m: float, log: Log) -> None:
        self._params = params
        self._sink_m = water_sink_m
        self._log = log

    def lower(self, dem: FloatMap, core: BoolMap, real: BoolMap, water_line: float) -> FloatMap:
        p, px = self._params, GAME_METERS_PER_PIXEL
        if not real.any() or (p.bed_ramp_game_m <= 0 and p.shore_smooth_game_m <= 0):
            return dem
        if p.bed_ramp_game_m > 0:
            d_bed = distance_transform_edt(real) * px
            if p.bed_smooth_game_m > 0:
                d_bed = np.where(real, gaussian_filter(d_bed, p.bed_smooth_game_m / px), 0)
            t_bed = np.clip(d_bed / p.bed_ramp_game_m, 0, 1)
            del d_bed
            dem[real] = (water_line - self._sink_m * t_bed * t_bed * (3 - 2 * t_bed))[real]
            del t_bed
        if p.shore_smooth_game_m > 0:
            ring = binary_dilation(real, iterations=max(1, round(p.shore_width_game_m / px)))
            ring &= ~core
            blurred = gaussian_filter(dem, p.shore_smooth_game_m / px)
            dem[ring] = np.maximum(blurred, water_line)[ring]
            self._log(f"real water smoothed: bed ramp {p.bed_ramp_game_m:g} m, shore strip {p.shore_width_game_m:g} m "
                      f"({ring.sum() * px ** 2 / 1e4:.0f} ha game area)")
        return dem


class LayerParams(NodeParams):
    border_game_m: float = param(25.0, ge=0, unit="game m")
    """Full border width around painted water (the bank border of `water.shape`)."""
    texture_shrink_game_m: float = param(0.0, ge=0, unit="game m")
    """Shrink the water texture inwards; the ring becomes shore (0 = no)."""
    real_shore_texture_share: float = param(0.5, ge=0, le=1)
    """Shore texture at real water only 0..this share of the border width."""
    real_shore_texture_wave_game_m: float = param(200.0, gt=0, unit="game m")
    """Wave length of the random shore width at real water."""


class WaterLayers(NamedTuple):
    water: Mask
    shore: Mask
    no_cover: Mask


@node("water.layers")
def layers(core: Mask, border: Mask, real: Mask, params: LayerParams, context: NodeContext) -> WaterLayers:
    """Water and shore import layers: water = core without real water (the game shows real water itself), optionally
    shrunk; shore = border + the cut-off ring, narrower and of random width at real water. `no_cover` = water and
    border, where no other layer may lie (no trees in the water)."""
    px = GAME_METERS_PER_PIXEL
    painted = core.values & ~real.values
    water = painted
    r = round(params.texture_shrink_game_m / px)
    if r > 0:
        yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
        water = binary_erosion(painted, structure=yy ** 2 + xx ** 2 <= r * r, border_value=1)
    shore_border = border.values
    if real.values.any():
        max_game_m = params.border_game_m * params.real_shore_texture_share
        shore_border = _narrow_at_real_water(border.values, real.values, painted, max_game_m, params, context)
    shore = shore_border | (painted & ~water)
    context.log(f"water texture: {int(water.sum())} px painted, {int(real.values.sum())} px real water without "
                f"texture")
    return WaterLayers(core.like(water), core.like(shore), core.like(core.values | border.values))


def _narrow_at_real_water(border: BoolMap, real: BoolMap, painted: BoolMap, max_game_m: float, params: LayerParams,
                          context: NodeContext) -> BoolMap:
    """A border pixel belongs to real water when it is nearer to real than to painted water."""
    px = GAME_METERS_PER_PIXEL
    d_real = distance_transform_edt(~real)
    d_paint = distance_transform_edt(~painted) if painted.any() else np.full(painted.shape, np.inf, np.float32)
    real_side = border & (d_real <= d_paint)
    del d_paint
    height, width = real.shape
    step = max(1, round(params.real_shore_texture_wave_game_m / 4 / px))
    rng = np.random.default_rng(_SHORE_NOISE_SEED)     # smooth random field 0..1 (uniform over ranks), coarse
    small = gaussian_filter(rng.standard_normal((height // step + 2, width // step + 2)), 1.0)
    small = (small.ravel().argsort().argsort() / (small.size - 1)).reshape(small.shape).astype(np.float32)
    noise = np.asarray(PilImage.fromarray(small, mode="F").resize((width, height), PilImage.Resampling.BILINEAR))
    width_px = noise * (max_game_m / px)
    shore = (border & ~real_side) | (real_side & (d_real <= width_px))
    context.log(f"shore texture at real water: 0..{max_game_m:g} m (instead of {params.border_game_m:g} m), "
                f"{(real_side & ~shore).sum() * px ** 2 / 1e4:.0f} ha without shore texture")
    return np.asarray(shore)
