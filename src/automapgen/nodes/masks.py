"""Mask nodes: masks from fields, filters and combinations."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import distance_transform_edt, gaussian_filter

from automapgen.graph import NodeParams, node, param
from automapgen.nodes.rasters import Field, Mask, resize_nearest


class LimitParams(NodeParams):
    limit: float
    """The pixel is set where the value is above (`mask.above`) or below (`mask.below`) this limit."""


@node("mask.above", output="mask")
def above(field: Field, params: LimitParams) -> Mask:
    """Pixels whose value is above the limit."""
    return Mask(field.values > params.limit, field.frame)


@node("mask.below", output="mask")
def below(field: Field, params: LimitParams) -> Mask:
    """Pixels whose value is below the limit."""
    return Mask(field.values < params.limit, field.frame)


class MaskSmoothParams(NodeParams):
    radius_m: float = param(80.0, gt=0, unit="m")
    """Gaussian radius (real metres): small specks vanish, borders become rounder."""
    threshold: float = param(0.5, gt=0, lt=1)
    """Share of set pixels around a pixel from which it stays set."""


@node("mask.smooth", output="mask")
def smooth(mask: Mask, params: MaskSmoothParams) -> Mask:
    """Majority filter: a pixel is set where more than `threshold` of its surroundings are set."""
    shares = gaussian_filter(mask.values.astype(np.float32), sigma=params.radius_m / mask.pixel_m)
    return mask.like(shares > params.threshold)


@node("mask.to_map", output="mask")
def to_map(mask: Mask) -> Mask:
    """The mask on the heightmap grid (nearest neighbour), e.g. for the game's import layers."""
    cells = mask.values.astype(np.uint8) * 255
    return mask.like(resize_nearest(cells, mask.frame.width_px, mask.frame.height_px) > 0)


@node("mask.subtract", output="mask")
def subtract(mask: Mask, minus: tuple[Mask, ...]) -> Mask:
    """Pixels of `mask` set in none of the `minus` masks (e.g. no forest on water)."""
    values = mask.values
    for m in minus:
        values = values & ~m.values
    return mask.like(values)


class NearParams(NodeParams):
    distance_px: float = param(1.0, ge=0)
    """Distance in pixels of the mask (heightmap grid: 1 px = 4 game m)."""


@node("mask.near", output="mask")
def near(mask: Mask, params: NearParams) -> Mask:
    """Pixels at most `distance_px` from the mask (the mask grown by a distance); empty for an empty mask."""
    if not mask.values.any():
        return mask
    return mask.like(distance_transform_edt(~mask.values) <= params.distance_px)


@node("mask.all", output="mask")
def all_of(masks: tuple[Mask, ...]) -> Mask:
    """Pixels set in every linked mask."""
    return _combine(masks, np.logical_and)


@node("mask.any", output="mask")
def any_of(masks: tuple[Mask, ...]) -> Mask:
    """Pixels set in at least one linked mask."""
    return _combine(masks, np.logical_or)


@node("mask.invert", output="mask")
def invert(mask: Mask) -> Mask:
    """Pixels not set in the mask."""
    return mask.like(~mask.values)


def _combine(masks: tuple[Mask, ...], operation: np.ufunc) -> Mask:
    if not masks:
        raise ValueError("needs at least one mask")
    values = masks[0].values
    for m in masks[1:]:
        if m.values.shape != values.shape:
            raise ValueError(f"masks of different size: {values.shape} and {m.values.shape}")
        values = operation(values, m.values)
    return masks[0].like(values)
