"""Field nodes: arithmetic and filters on real-valued rasters."""

from __future__ import annotations

from typing import Literal

import numpy as np
from PIL import Image as PilImage
from scipy.ndimage import gaussian_filter, minimum_filter

from automapgen.graph import NodeParams, node, param
from automapgen.nodes.rasters import Field, resize_floats

_METHODS = {"box": PilImage.Resampling.BOX, "bilinear": PilImage.Resampling.BILINEAR,
            "bicubic": PilImage.Resampling.BICUBIC, "nearest": PilImage.Resampling.NEAREST}


class ResampleParams(NodeParams):
    work_scale: int = param(4, ge=1)
    """Result has 1/work_scale of the heightmap resolution (1 = heightmap grid)."""
    method: Literal["box", "bilinear", "bicubic", "nearest"] = "box"
    """Box averages (coarser grids); bicubic/bilinear interpolate (finer grids)."""


@node("field.resample", output="field")
def resample(field: Field, params: ResampleParams) -> Field:
    """The field on a grid of 1/work_scale of the heightmap resolution."""
    width, height = field.frame.work_grid(params.work_scale)
    return field.like(resize_floats(field.values, width, height, _METHODS[params.method]))


@node("field.slope", output="degrees")
def slope(field: Field) -> Field:
    """Slope of a height field in degrees."""
    mpp_x, mpp_y = field.meters_per_pixel
    gy, gx = np.gradient(field.values, mpp_y, mpp_x)
    return field.like(np.degrees(np.arctan(np.hypot(gx, gy))))


class NoiseParams(NodeParams):
    seed: int = 1
    """Random seed; the same seed gives the same noise."""
    wave_m: float = param(300.0, gt=0, unit="m")
    """Typical size of the bumps (real metres)."""
    amplitude: float = param(80.0, ge=0)
    """Standard deviation of the result."""


@node("field.noise", output="noise")
def noise(like: Field, params: NoiseParams) -> Field:
    """Smooth random noise on the grid of `like`."""
    rng = np.random.default_rng(params.seed)
    values = gaussian_filter(rng.standard_normal((like.height, like.width)).astype(np.float32),
                             sigma=params.wave_m / like.pixel_m)
    values *= params.amplitude / max(float(values.std()), 1e-6)
    return like.like(values)


class RampParams(NodeParams):
    start: float = 0.0
    """Below this value the result is 0."""
    per_unit: float = 1.0
    """Increase of the result per unit above `start`."""


@node("field.ramp", output="field")
def ramp(field: Field, params: RampParams) -> Field:
    """max(0, value - start) x per_unit, e.g. extra height for steep slopes."""
    return field.like(np.maximum(0.0, field.values - params.start) * params.per_unit)


@node("field.sum", output="field")
def total(fields: tuple[Field, ...]) -> Field:
    """Pixel-wise sum of the linked fields (same grid), in link order."""
    if not fields:
        raise ValueError("needs at least one field")
    values = fields[0].values
    for f in fields[1:]:
        if f.values.shape != values.shape:
            raise ValueError(f"fields of different size: {values.shape} and {f.values.shape}")
        values = values + f.values
    return fields[0].like(values)


class SmoothParams(NodeParams):
    sigma_px: float = param(1.0, gt=0)
    """Gaussian smoothing radius in pixels of the field."""


@node("field.smooth", output="field")
def smooth(field: Field, params: SmoothParams) -> Field:
    """Gaussian smoothing."""
    return field.like(gaussian_filter(field.values, sigma=params.sigma_px))


class RadiusParams(NodeParams):
    radius_px: int = param(1, ge=1)
    """Radius in pixels of the field (heightmap grid: 1 px = 4 game m)."""


@node("field.min_within", output="field")
def min_within(field: Field, params: RadiusParams) -> Field:
    """Lowest value within a circle around each pixel, e.g. the valley floor next to a river."""
    r = params.radius_px
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    return field.like(minimum_filter(field.values, footprint=xx ** 2 + yy ** 2 <= r ** 2))


@node("field.at_least", output="field")
def at_least(field: Field, minimum: float) -> Field:
    """Raises every value below `minimum` to it (e.g. land below the water line)."""
    return field.like(np.maximum(field.values, np.float32(minimum)))
