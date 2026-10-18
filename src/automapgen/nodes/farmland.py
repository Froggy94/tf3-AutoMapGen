"""Fields: cropland from OSM fields (grouped, one crop per group) or from WorldCover cut into parcels; crop types.

Sizes in game metres / game hectares (fixed pixel counts, independent of the map scale). Crop type 0 = base area
(grass in the mod), 1.. = the keys of the crop plan's `shares` in order.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

import numpy as np
from numpy.typing import NDArray
from PIL import Image as PilImage
from PIL import ImageDraw
from pydantic import BaseModel, ConfigDict, model_validator
from scipy.ndimage import distance_transform_edt, find_objects, label
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import breadth_first_order, minimum_spanning_tree
from scipy.spatial import cKDTree

from automapgen.graph import NodeContext, NodeParams, data_type, node, param
from automapgen.nodes.documents import JsonDocument, JsonDocumentCodec
from automapgen.nodes.images import Image
from automapgen.nodes.maps import GAME_METERS_PER_PIXEL, MapProjection
from automapgen.nodes.osm import Area, OsmAreas
from automapgen.nodes.rasters import ClassMap, Field, Labels, Mask

GRASS = "grass"                                # crop key of the base area (no crop layer; the meadow look)
_PX = GAME_METERS_PER_PIXEL
_HA_PX = 1e4 / _PX ** 2                        # pixels per game hectare
_CELL = 100_000                                # parcel cell code = row x _CELL + column
_MIN_FARM_COVER = 0.5                          # farm grid: cells with at least this share of cropland
_BLOCK_SEED_JITTER = 0.35
_CROP_COLORS = np.array([(90, 170, 60), (230, 200, 60), (240, 140, 20), (120, 80, 50), (200, 120, 200),
                         (60, 160, 200)], dtype=np.float32)
_UNSPLIT_COLOR = (150, 140, 90)                # cropland without OSM field / not divided
_EDGE_COLOR = (60, 60, 60)

type BoolMap = NDArray[np.bool_]
type Log = Callable[[str], None]


class _Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class CropSizeClass(_Settings):
    min_game_ha: float
    """Size class from this group size (game hectares); the first class reached wins."""
    shares: dict[str, float]
    """Crop key or "grass" -> share."""


class CropPlan(_Settings):
    """Crop types of the fields (a value of the climate preset); each key needs an import layer crop_<key>."""

    shares: dict[str, float] = {"wheat": 0.4, "corn": 0.2, "soil": 0.1}
    """Crop key -> share of the cropland (parcels from WorldCover); the rest is grass."""
    by_size: tuple[CropSizeClass, ...] = (
        CropSizeClass(min_game_ha=1.5, shares={"wheat": 0.5, "corn": 0.3, "grass": 0.1, "soil": 0.1}),
        CropSizeClass(min_game_ha=0.3, shares={"grass": 0.6, "wheat": 0.3, "soil": 0.1}),
        CropSizeClass(min_game_ha=0, shares={"grass": 1}))
    """Crop shares of OSM field groups by size class."""
    osm_tags: dict[str, str] = {"maize": "corn", "corn": "corn", "grass": "", "hay": "", "wheat": "wheat",
                                "barley": "wheat", "rye": "wheat", "oats": "wheat", "cereal": "wheat"}
    """OSM crop= -> crop key ("" = grass)."""

    @model_validator(mode="after")
    def _known_keys(self) -> CropPlan:
        keys = set(self.shares) | {GRASS}
        unknown = sorted({k for c in self.by_size for k in c.shares} - keys)
        unknown += sorted({v for v in self.osm_tags.values() if v} - keys)
        if unknown:
            raise ValueError(f"{unknown} are no crop keys (shares: {sorted(self.shares)})")
        return self


# --- fields from OSM -----------------------------------------------------------------------------------------------


class OsmFieldParams(NodeParams):
    seed: int = 3
    """Random seed of the crop choice."""
    merge_gap_game_m: float = param(8.0, ge=0, unit="game m")
    """Fields with at most this gap count as neighbours."""
    min_game_ha: float = param(1.0, ge=0, unit="game ha")
    """Smaller groups are merged with neighbours."""
    max_game_ha: float = param(4.0, ge=0, unit="game ha")
    """Merging only up to this group size."""
    crops: CropPlan = CropPlan()
    """Crop types (from the climate preset)."""


class OsmFields(NamedTuple):
    fields: Mask
    groups: Labels
    crop_type: ClassMap
    group_count: int


@node("farmland.osm_fields")
def osm_fields(areas: OsmAreas, no_cover: Mask, params: OsmFieldParams, context: NodeContext) -> OsmFields:
    """OSM fields rasterised (not on water/border), small neighbours merged into groups, one crop per group (from the
    OSM crop tag, else drawn by size class so that neighbours differ). `fields` = all field pixels, `crop_type` = crop
    per pixel (0 = grass)."""
    frame = no_cover.frame
    fields = areas.areas
    context.log(f"{len(fields)} OSM field areas")
    lab = _rasterise(fields, MapProjection(frame), frame.width_px, frame.height_px)
    lab[no_cover.values] = 0
    groups = _merge(lab, len(fields) + 1, params)
    types, n_tagged = _CropChooser(params, context.log).choose(fields, groups)
    group = groups.root[lab]
    sizes = groups.area[groups.groups] / _HA_PX
    context.log(f"{len(fields)} OSM fields -> {len(groups.groups)} groups (gap {params.merge_gap_game_m:g} m, target "
                f"{params.min_game_ha:g}-{params.max_game_ha:g} ha), crop from OSM tag: {n_tagged}; groups median "
                f"{np.median(sizes) if len(sizes) else 0:.2f} ha (game area)")
    return OsmFields(no_cover.like(group > 0), Labels(group, frame), ClassMap(types[group], frame), len(types))


def _rasterise(fields: Sequence[Area], projection: MapProjection, width: int, height: int) -> NDArray[np.int32]:
    canvas = PilImage.new("I", (width, height), 0)
    draw = ImageDraw.Draw(canvas)

    def to_px(ring: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
        return [(u * width - 0.5, v * height - 0.5) for u, v in projection.ring_to_uv(ring)]

    for i, area in enumerate(fields, 1):
        for ring in area.outer:
            draw.polygon(to_px(ring), fill=i)
        for ring in area.inner:
            draw.polygon(to_px(ring), fill=0)
    return np.asarray(canvas, dtype=np.int32).copy()


@dataclass
class _Groups:
    """OSM fields merged into groups: root per field, area per root, neighbour border lengths."""

    root: NDArray[np.int64]
    area: NDArray[np.int64]
    neighbours: dict[int, dict[int, int]]
    groups: list[int] = field(default_factory=list)


def _merge(lab: NDArray[np.int32], n: int, p: OsmFieldParams) -> _Groups:
    """Smallest group first, merged with the neighbour of the longest common border."""
    area = np.bincount(lab.ravel(), minlength=n).astype(np.int64)
    area[0] = 0
    gap = p.merge_gap_game_m / _PX               # pixels up to half the gap belong to the nearest field
    dist, idx = distance_transform_edt(lab == 0, return_indices=True)
    near = lab[idx[0], idx[1]]
    near[dist > (gap + 1) / 2] = 0
    del dist, idx
    codes = []
    for a_, b_ in ((near[:, 1:], near[:, :-1]), (near[1:, :], near[:-1, :])):
        d = (a_ != b_) & (a_ > 0) & (b_ > 0)
        codes.append(np.minimum(a_[d], b_[d]).astype(np.int64) * n + np.maximum(a_[d], b_[d]))
    del near
    pairs, border = np.unique(np.concatenate(codes), return_counts=True)
    nb: dict[int, dict[int, int]] = {i: {} for i in range(1, n)}
    for pair, length in zip(pairs, border, strict=True):
        a, b = int(pair // n), int(pair % n)
        nb[a][b] = nb[b][a] = int(length)
    min_px, max_px = p.min_game_ha * _HA_PX, p.max_game_ha * _HA_PX
    root = np.arange(n)
    heap = [(int(area[i]), i) for i in range(1, n) if 0 < area[i] < min_px]
    heapq.heapify(heap)
    while heap:
        a_size, i = heapq.heappop(heap)
        if root[i] != i or area[i] != a_size:
            continue
        candidates = [(length, j) for j, length in nb[i].items() if area[i] + area[j] <= max_px]
        if not candidates:
            continue
        j = max(candidates)[1]
        root[root == i] = j
        area[j] += area[i]
        area[i] = 0
        for k, length in nb.pop(i).items():
            nb[k].pop(i)
            if k != j:
                nb[j][k] = nb[k][j] = nb[j].get(k, 0) + length
        if area[j] < min_px:
            heapq.heappush(heap, (int(area[j]), j))
    return _Groups(root, area, nb, [i for i in range(1, n) if root[i] == i and area[i] > 0])


class _CropChooser:
    """Shares by area per size class: large groups first, crop drawn by the area it still lacks in its class (without
    the crops of finished neighbours where possible)."""

    def __init__(self, params: OsmFieldParams, log: Log) -> None:
        self._params = params
        self._log = log

    def choose(self, fields: Sequence[Area], g: _Groups) -> tuple[NDArray[np.uint8], int]:
        crops = self._params.crops
        keys = list(crops.shares)
        shares = np.array([[c.shares.get(GRASS, 0.0)] + [c.shares.get(k, 0.0) for k in keys] for c in crops.by_size])
        shares /= shares.sum(axis=1, keepdims=True)
        min_cls = np.array([c.min_game_ha * _HA_PX for c in crops.by_size])
        n = len(g.root)
        cls = np.full(n, len(min_cls) - 1)
        for c in range(len(min_cls) - 1, -1, -1):
            cls[g.area >= min_cls[c]] = c
        rng = np.random.default_rng(self._params.seed)
        types = np.zeros(n, dtype=np.uint8)
        done = np.zeros(n, dtype=bool)
        for i in range(1, n):
            crop = fields[i - 1].tags.get("crop")
            tag = crops.osm_tags.get(crop) if crop is not None else None
            if tag is not None and g.area[g.root[i]] > 0 and not done[g.root[i]]:
                types[g.root[i]] = keys.index(tag) + 1 if tag in keys else 0
                done[g.root[i]] = True
        n_tagged = int(done.sum())
        groups = np.array(g.groups, dtype=np.int64)
        missing = np.zeros_like(shares)
        for c in range(len(min_cls)):
            sel = groups[cls[groups] == c]
            tagged = sel[done[sel]]
            missing[c] = shares[c] * float(g.area[sel].sum()) - np.bincount(
                types[tagged], weights=g.area[tagged], minlength=shares.shape[1])
        order = rng.permutation(groups)
        for i in order[np.argsort(-g.area[order], kind="stable")]:
            if done[i]:
                continue
            c = cls[i]
            w = np.maximum(missing[c], 0) * (shares[c] > 0)
            w_nb = w.copy()
            w_nb[np.array([types[j] for j in g.neighbours[i] if done[j]], dtype=np.int64)] = 0
            if w_nb.sum() > 0:
                w = w_nb
            elif w.sum() <= 0:
                w = shares[c].copy()
            types[i] = rng.choice(len(w), p=w / w.sum())
            missing[c, types[i]] -= g.area[i]
            done[i] = True
        self._log_classes(groups, cls, types, g.area, shares, [GRASS, *keys])
        return types, n_tagged

    def _log_classes(self, groups: NDArray[np.int64], cls: NDArray[Any], types: NDArray[np.uint8],
                     area: NDArray[np.int64], shares: NDArray[Any], names: list[str]) -> None:
        for c, size_class in enumerate(self._params.crops.by_size):
            sel = groups[cls[groups] == c]
            total = max(float(area[sel].sum()), 1.0)
            got = np.bincount(types[sel], weights=area[sel], minlength=len(names)) / total * 100
            self._log(f"from {size_class.min_game_ha:g} ha: {len(sel)} groups, {total / _HA_PX:.0f} ha - " + ", ".join(
                f"{k} {v:.0f} %" for k, v in zip(names, got, strict=True) if v > 0 or shares[c, names.index(k)] > 0))


class PreviewParams(NodeParams):
    seed: int = 3
    """The seed of the field node (brightness per group)."""


@node("farmland.osm_preview", output="preview")
def osm_preview(crop_type: ClassMap, groups: Labels, group_count: int, cropland: Mask,
                params: PreviewParams) -> Image:
    """Half resolution: colour per crop, brightness per group, group borders dark; cropland without OSM field in
    khaki."""
    shade = np.random.default_rng(params.seed).uniform(0.75, 1.05, group_count).astype(np.float32)
    g2 = groups.values[::2, ::2]
    prev = _CROP_COLORS[crop_type.values[::2, ::2]] * shade[g2][..., None]
    edge = np.zeros(g2.shape, dtype=bool)
    edge[:, 1:] |= g2[:, 1:] != g2[:, :-1]
    edge[1:, :] |= g2[1:, :] != g2[:-1, :]
    prev[edge & (g2 > 0)] = _EDGE_COLOR
    prev[g2 == 0] = 0
    prev[cropland.values[::2, ::2] & (g2 == 0)] = _UNSPLIT_COLOR
    return Image(prev.astype(np.uint8))


# --- fields from WorldCover cropland, cut into parcels -------------------------------------------------------------


class GapParams(NodeParams):
    gap_game_m: float = param(100.0, ge=0, unit="game m")
    """Gaps of `other` up to this width inside the cropland become cropland."""


class ClosedGap(NamedTuple):
    cropland: Mask
    other: Mask


@node("farmland.close_gap")
def close_gap(cropland: Mask, other: Mask, keep: Mask, params: GapParams, context: NodeContext) -> ClosedGap:
    """Narrow strips of another layer (meadow, forest) between fields become cropland - except `keep` (e.g.
    settlements)."""
    r = params.gap_game_m / _PX / 2
    if r < 0.5 or not cropland.values.any():
        return ClosedGap(cropland, other)
    crop = cropland.values
    closed = distance_transform_edt(distance_transform_edt(~crop) <= r) > r
    add = closed & ~crop & other.values & ~keep.values
    context.log(f"gaps up to {params.gap_game_m:g} m closed, {add.sum() * _PX ** 2 / 1e4:.0f} ha game area to "
                f"cropland")
    return ClosedGap(cropland.like(crop | add), other.like(other.values & ~add))


@data_type("farm_grid", "Field blocks of the parcels: rectangle grids per block with the cropland share per cell.",
           codec=JsonDocumentCodec(lambda data: FarmGrid(data)),
           preview=lambda g: f"{len(g.data.get('blocks', []))} field blocks")
@dataclass(frozen=True)
class FarmGrid(JsonDocument):
    """{width_px, height_px, game_m_per_px, blocks: [{area, angle, s, t, cells}]}."""


class ParcelParams(NodeParams):
    seed: int = 3
    """Random seed of the parcels and the crop choice."""
    min_split_game_ha: float = param(1.0, ge=0, unit="game ha")
    """Smaller fields are not divided."""
    block_game_m: float = param(2500.0, gt=0, unit="game m")
    """Large areas in field blocks of about this size."""
    block_turn_deg: float = param(10.0, ge=0, unit="deg")
    """Block direction differs from the area by at most this."""
    width_game_m: tuple[float, float] = (50.0, 150.0)
    """Strip width across the working direction (game m)."""
    length_game_m: tuple[float, float] = (100.0, 300.0)
    """Parcel length along it (game m)."""
    min_aspect: float = 1.5
    """Length at least this times the width."""
    jitter: float = 0.15
    """Line spacing varied by up to this share."""
    min_side_game_m: float = param(50.0, gt=0, unit="game m")
    """Smallest side (reference of the minimum area)."""
    min_area_share: float = 0.5
    """Edge pieces smaller than share x min side² join a neighbour."""
    size_bias: float = 0.5
    """Exponent of the random size (< 1 = larger parcels more often)."""
    contour_slope_deg: float = param(4.0, ge=0, unit="deg")
    """From this mean slope: parcels along the contour lines."""
    crops: CropPlan = CropPlan()
    """Crop types (from the climate preset)."""


class Parcels(NamedTuple):
    parcels: Labels
    crop_type: ClassMap
    farm_grid: FarmGrid
    preview: Image


@node("farmland.parcels")
def parcels(cropland: Mask, heights: Field, params: ParcelParams, context: NodeContext) -> Parcels:
    """Each field area in a grid of rectangles (along the contour lines on slopes, else along its main axis), large
    areas in field blocks first; one crop per parcel by the crop plan's shares."""
    return _ParcelDivider(params, cropland.meters_per_pixel, context.log).divide(cropland, heights)


class _ParcelDivider:
    def __init__(self, params: ParcelParams, meters_per_pixel: tuple[float, float], log: Log) -> None:
        self._params = params
        self._mpp = meters_per_pixel
        self._log = log

    def divide(self, cropland_mask: Mask, heights: Field) -> Parcels:
        pp, cropland, dem = self._params, cropland_mask.values, heights.values
        rng = np.random.default_rng(pp.seed)
        splitter = _ParcelSplitter(pp, rng)
        min_split = pp.min_split_game_ha * _HA_PX
        lab, n_fields = label(cropland, structure=np.ones((3, 3)))
        parcel = np.zeros(lab.shape, dtype=np.int32)   # parcel ID per pixel, 0 = plain cropland
        block_px = pp.block_game_m / _PX
        turn = np.radians(pp.block_turn_deg)
        next_id, n_split, n_blocks = 1, 0, 0
        mpp_x, mpp_y = self._mpp
        for i, sl in enumerate(find_objects(lab), 1):
            if sl is None:
                continue
            m = lab[sl] == i
            if m.sum() < min_split:                    # too small: plain cropland
                continue
            yy, xx = np.nonzero(m)
            gy, gx = np.gradient(dem[sl], mpp_y, mpp_x)
            gx, gy = gx[m], gy[m]
            base_angle = splitter.contour_angle(gx, gy)
            if base_angle is None:
                base_angle = splitter.axis_angle(xx, yy)
            h, w = m.shape
            if max(h, w) > 1.5 * block_px:             # field blocks: Voronoi around jittered grid points
                sx = np.arange(block_px / 2, w + block_px / 2, block_px)
                sy = np.arange(block_px / 2, h + block_px / 2, block_px)
                seeds = np.array([(x, y) for y in sy for x in sx], dtype=np.float64)
                seeds += rng.uniform(-_BLOCK_SEED_JITTER, _BLOCK_SEED_JITTER, seeds.shape) * block_px
                block = cKDTree(seeds).query(np.column_stack([xx, yy]), workers=-1)[1]
            else:
                block = np.zeros(len(yy), dtype=np.int64)
            blocks = np.unique(block)
            out = np.zeros(len(yy), dtype=np.int64)
            for b in blocks:
                sel = block == b
                if sel.sum() < min_split:              # rest of a block: plain cropland
                    continue
                if len(blocks) > 1:
                    angle = splitter.contour_angle(gx[sel], gy[sel])
                    if angle is None:
                        angle = base_angle + rng.uniform(-turn, turn)
                else:
                    angle = base_angle
                idx, k = splitter.divide(i, yy[sel] + sl[0].start, xx[sel] + sl[1].start, angle)
                out[sel] = next_id + idx
                next_id += k
                n_blocks += 1
            parcel[sl][m] = out
            n_split += 1
        del lab
        height, width = cropland.shape
        farm_grid = {"width_px": width, "height_px": height, "game_m_per_px": _PX, "blocks": splitter.blocks}
        self._log(f"farm grid: {len(splitter.blocks)} field blocks, "
                  f"{sum(len(b['cells']) for b in splitter.blocks)} cells with >= 50 % cropland")
        crop_type = self._crop_types(rng, next_id)[parcel]
        sizes = np.bincount(parcel.ravel(), minlength=next_id)[1:] / _HA_PX
        if len(sizes):
            self._log(f"{n_fields} field areas, {n_split} divided ({n_blocks} field blocks, "
                      f"{(parcel > 0).sum() / max(int(cropland.sum()), 1) * 100:.0f} % of the cropland); "
                      f"{next_id - 1} parcels, median {np.median(sizes):.2f} ha game area")
        preview = self._preview(rng, parcel, crop_type, next_id, cropland)
        frame = cropland_mask.frame
        return Parcels(Labels(parcel, frame), ClassMap(crop_type, frame), FarmGrid(farm_grid), Image(preview))

    def _crop_types(self, rng: np.random.Generator, n: int) -> NDArray[np.uint8]:
        u = rng.random(n)
        types = np.zeros(n, dtype=np.uint8)
        acc = 0.0
        for i, share in enumerate(self._params.crops.shares.values(), 1):
            types[(u >= acc) & (u < acc + share)] = i
            acc += share
        types[0] = 0                                   # plain cropland (base area grass)
        return types

    @staticmethod
    def _preview(rng: np.random.Generator, parcel: NDArray[np.int32], crop_type: NDArray[np.uint8], n: int,
                 cropland: BoolMap) -> NDArray[np.uint8]:
        """Half resolution: colour per crop, brightness per parcel."""
        shade = rng.uniform(0.7, 1.05, n).astype(np.float32)
        p2 = parcel[::2, ::2]
        prev = _CROP_COLORS[crop_type[::2, ::2]] * shade[p2][..., None]
        prev[p2 == 0] = 0
        prev[cropland[::2, ::2] & (p2 == 0)] = _UNSPLIT_COLOR
        return np.asarray(prev.astype(np.uint8))


class _ParcelSplitter:
    """Rectangle grid of one field block; collects the farm grid per block."""

    def __init__(self, params: ParcelParams, rng: np.random.Generator) -> None:
        self._p = params
        self._rng = rng
        self._width = tuple(v / _PX for v in params.width_game_m)
        self._length = tuple(v / _PX for v in params.length_game_m)
        self._min_area = params.min_area_share * (params.min_side_game_m / _PX) ** 2
        self.blocks: list[dict[str, Any]] = []

    def _draw(self, lo: float, hi: float) -> float:
        return float(lo + (hi - lo) * self._rng.random() ** self._p.size_bias)   # larger preferred

    def _grid(self, v: NDArray[Any], step: float) -> tuple[NDArray[np.int64], NDArray[Any]]:
        """Index in a grid of spacing ~step (+- jitter), without rest strips at the ends."""
        lo, hi = v.min(), v.max() + 1
        n = max(1, round((hi - lo) / step))
        jitter = self._p.jitter
        pos = lo + (hi - lo) * (np.arange(1, n) + self._rng.uniform(-jitter, jitter, n - 1)) / n
        return np.searchsorted(pos, v), np.concatenate([[lo], pos, [hi]])

    @staticmethod
    def axis_angle(xx: NDArray[Any], yy: NDArray[Any]) -> float:
        ev = np.linalg.eigh(np.cov(np.vstack([xx, yy]).astype(np.float64)))[1][:, -1]
        return float(np.arctan2(ev[1], ev[0]))

    def contour_angle(self, gx: NDArray[Any], gy: NDArray[Any]) -> float | None:
        """Direction of the contour lines, or None if flatter than contour_slope_deg."""
        mgx, mgy = gx.mean(), gy.mean()
        if np.degrees(np.arctan(np.hypot(mgx, mgy))) > self._p.contour_slope_deg:
            return float(np.arctan2(mgy, mgx) + np.pi / 2)
        return None

    def divide(self, area_id: int, yy: NDArray[Any], xx: NDArray[Any], angle: float) -> tuple[NDArray[np.int64], int]:
        """Area (pixels yy, xx) in a rectangle grid -> parcel index per pixel (0..k-1), k."""
        ca, sa = np.cos(angle), np.sin(angle)
        s_ = xx * ca + yy * sa                         # along
        t_ = -xx * sa + yy * ca                        # across
        width = self._draw(*self._width)
        length = max(self._draw(*self._length), min(width * self._p.min_aspect, self._length[1]))
        row, t_edges = self._grid(t_, width)
        col, s_edges = self._grid(s_, length)
        cell = row * _CELL + col
        cid, ccnt = np.unique(cell, return_counts=True)            # farm grid: cells with their cropland share
        r_, c_ = cid // _CELL, cid % _CELL
        cover = ccnt / (np.diff(t_edges)[r_] * np.diff(s_edges)[c_])
        keep = cover >= _MIN_FARM_COVER
        self.blocks.append({
            "area": area_id, "angle": round(float(angle), 6),
            "s": [round(float(v), 2) for v in s_edges], "t": [round(float(v), 2) for v in t_edges],
            "cells": [[int(a), int(b), round(float(c), 3)] for a, b, c in zip(r_[keep], c_[keep], cover[keep],
                                                                               strict=True)]})
        _, inv, cnt = np.unique(cell, return_inverse=True, return_counts=True)
        big = cnt >= self._min_area
        if not big.any():
            return np.zeros(len(yy), dtype=np.int64), 1
        small_px = ~big[inv]                           # too small edge pieces join the nearest large piece
        if small_px.any():
            tree = cKDTree(np.column_stack([xx[~small_px], yy[~small_px]]))
            nearest = tree.query(np.column_stack([xx[small_px], yy[small_px]]), workers=-1)[1]
            inv[small_px] = inv[~small_px][nearest]
        used, inv = np.unique(inv, return_inverse=True)
        return inv, len(used)


class FieldPathParams(NodeParams):
    range_game_m: float = param(32.0, gt=0, unit="game m")
    """Distance for grey 0 / 255."""


@node("farmland.field_paths", output="image")
def field_paths(parcels: Labels, params: FieldPathParams, context: NodeContext) -> Image:
    """Signed distance to the parcel borders (grey 127.5 = border), for paths between the fields: parcels two-coloured
    along a maximum spanning tree of the border lengths, so conflicts remain only at short contacts."""
    parcel = parcels.values
    n = int(parcel.max()) + 1
    border = np.zeros(parcel.shape, dtype=bool)
    codes = []
    for a_, b_ in (((slice(None), slice(1, None)), (slice(None), slice(None, -1))),
                   ((slice(1, None), slice(None)), (slice(None, -1), slice(None)))):
        pa, pb = parcel[a_], parcel[b_]
        d = (pa != pb) & (pa > 0) & (pb > 0)
        border[a_] |= d
        border[b_] |= d
        codes.append(np.minimum(pa[d], pb[d]).astype(np.int64) * n + np.maximum(pa[d], pb[d]))
    pairs, weight = np.unique(np.concatenate(codes), return_counts=True)   # border length in pixels
    range_px = params.range_game_m / _PX
    if not len(pairs):
        return Image(np.full(parcel.shape, 255, dtype=np.uint8))
    pu, pv = pairs // n, pairs % n
    tree = minimum_spanning_tree(csr_matrix((weight.max() + 1.0 - weight, (pu, pv)), shape=(n, n)))
    tree = (tree + tree.T).tocsr()
    color = np.zeros(n, dtype=np.int8)                 # 0 = not yet, 1 / -1
    for start in range(1, n):
        if color[start]:
            continue
        order, pred = breadth_first_order(tree, start, directed=False, return_predecessors=True)
        color[start] = 1
        for v_ in order[1:]:
            color[v_] = -color[pred[v_]]
    bad = color[pu] == color[pv]
    context.log(f"colouring: {int(bad.sum())} of {len(pairs)} neighbour pairs without sign change")
    dist = np.minimum(distance_transform_edt(~border) + 0.5, range_px).astype(np.float32)
    idx = distance_transform_edt(parcel == 0, return_distances=False, return_indices=True)
    sign = color[parcel[idx[0], idx[1]]].astype(np.float32)
    dist[parcel == 0] = range_px
    return Image(np.clip(np.rint(127.5 + 127.5 * sign * dist / range_px), 0, 255).astype(np.uint8))
