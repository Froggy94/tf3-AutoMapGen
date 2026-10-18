"""Painted riverbed of the river plan: a mask on the heightmap grid (10-20 m are only a few pixels) and the groove
along its centre (the only terrain change of painted rivers)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray
from PIL import Image as PilImage
from PIL import ImageDraw
from scipy.ndimage import distance_transform_edt

from automapgen.graph import NodeContext, NodeParams, node, param
from automapgen.nodes.maps import GAME_METERS_PER_PIXEL, MapFormat
from automapgen.nodes.rasters import ClassMap, Field, Mask, resize_floats
from automapgen.nodes.water import WATER_CLASS, BedSegment, BedSegments

_ABOVE_WATER_LINE_GAME_M = 1.0


class Riverbed(NamedTuple):
    bed: Mask
    centre: Mask


@node("riverbed.paint")
def paint(beds: BedSegments, no_cover: Mask, context: NodeContext) -> Riverbed:
    """The painted riverbed pieces as a mask, not on water and its border (`no_cover`); `centre` = 1-pixel centre
    line for the groove."""
    frame = no_cover.frame
    width, height = frame.width_px, frame.height_px
    bed = _draw(beds.segments, width, height, lambda s: max(1, round(s.width_game_m / GAME_METERS_PER_PIXEL)))
    centre = _draw(beds.segments, width, height, lambda s: 1)
    bed &= ~no_cover.values
    centre &= ~no_cover.values
    context.log(f"painted riverbed: {len(beds.segments)} pieces, "
                f"{bed.sum() * GAME_METERS_PER_PIXEL ** 2 / 1e4:.0f} ha game area")
    return Riverbed(no_cover.like(bed), no_cover.like(centre))


def _draw(segments: Sequence[BedSegment], width: int, height: int,
          width_px: Callable[[BedSegment], int]) -> NDArray[np.bool_]:
    canvas = PilImage.new("L", (width, height), 0)
    draw = ImageDraw.Draw(canvas)
    w1, h1 = width - 1, height - 1
    for s in segments:
        (u0, v0), (u1, v1) = s.start, s.end
        draw.line([(u0 * w1, v0 * h1), (u1 * w1, v1 * h1)], fill=255, width=width_px(s), joint="curve")
    return np.asarray(canvas) > 0


class GrooveParams(NodeParams):
    depth_game_m: float = param(4.0, ge=0, unit="game m")
    """Depth of the groove (0 = none)."""
    width_game_m: float = param(4.0, ge=0, unit="game m")
    """Flat bottom."""
    smooth_game_m: float = param(8.0, ge=0, unit="game m")
    """Cosine flanks."""


@node("riverbed.groove", output="heights")
def groove(heights: Field, centre: Mask, land_min_m: float, map_format: MapFormat, params: GrooveParams,
           context: NodeContext) -> Field:
    """Groove along the centre line: depth relative to the terrain (follows its slope), soft flanks, never below the
    water line + 1 m (game). It makes a painted river look like a river course."""
    if params.depth_game_m <= 0 or not centre.values.any():
        return heights
    p, scale, dem = params, map_format.game_per_real, heights.values
    d_game = distance_transform_edt(~centre.values).astype(np.float32) * GAME_METERS_PER_PIXEL
    t = np.clip((d_game - p.width_game_m / 2) / max(p.smooth_game_m, 1e-6), 0, 1)
    profile = 0.5 * (1 + np.cos(np.pi * t))                     # 1 in the centre, 0 outside
    sink = (p.depth_game_m / scale) * profile                   # real metres
    floor = np.minimum(dem, np.float32(land_min_m + _ABOVE_WATER_LINE_GAME_M / scale))
    new = np.maximum(dem - sink, floor)
    sel = profile > 0
    context.log(f"groove: {p.depth_game_m:g} m deep (game), {p.width_game_m:g} m wide, flanks {p.smooth_game_m:g} m; "
                f"{int(sel.sum())} pixels, mean lowering {float((dem - new)[sel].mean()) * scale:.2f} m (game)")
    return heights.like(np.asarray(new, dtype=np.float32))


class LandcoverParams(NodeParams):
    riverbed_class: int = param(210, ge=0, le=255)
    """Own landcover class of the painted riverbed."""
    shore_class: int = param(200, ge=0, le=255)
    """Own landcover class of the shore (stays)."""


@node("riverbed.landcover", output="landcover")
def landcover(landcover: ClassMap, bed: Mask, params: LandcoverParams) -> ClassMap:
    """Landcover cells more than half covered by the painted riverbed get the riverbed class (water and shore stay)."""
    rows, cols = landcover.values.shape
    bed_lc = resize_floats(bed.values, cols, rows, PilImage.Resampling.BOX) > 0.5
    values = landcover.values.copy()
    values[bed_lc & (values != WATER_CLASS) & (values != params.shore_class)] = params.riverbed_class
    return landcover.like(values)
