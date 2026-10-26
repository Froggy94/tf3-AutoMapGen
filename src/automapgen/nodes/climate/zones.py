"""Transition masks: where on the map the transition to the second look has begun (yes/no per heightmap pixel).

The game reads import layers as yes/no (any grey above 0 counts as yes, docsAI/notes/tf3_facts.md), so the mask only
marks the area; the gradual rise is computed in the climate mod from the distance to the start line (`ZoneRamp`).
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

from automapgen.graph import NodeContext, NodeParams, node
from automapgen.nodes.climate.preset import GradientZone, Transition
from automapgen.nodes.maps import MapFrame, MapProjection
from automapgen.nodes.rasters import Mask


class ZoneMask:
    """The area of a zone on the heightmap grid of a frame."""

    def __init__(self, frame: MapFrame) -> None:
        self._frame = frame

    def beyond_start(self, zone: GradientZone) -> NDArray[np.bool_]:
        """True where the transition has begun (beyond the start latitude, towards the end)."""
        f = self._frame
        projection = MapProjection(f)
        if f.uv_transform is not None:
            result = np.empty((f.height_px, f.width_px), dtype=np.bool_)
            us = np.linspace(0, 1, f.width_px)
            # Bounded batches: a large heightmap must not allocate millions of Python coordinate tuples at once.
            for start in range(0, f.height_px, 32):
                stop = min(start + 32, f.height_px)
                uv = [(u, row / (f.height_px - 1)) for row in range(start, stop) for u in us]
                lat = np.array([p[1] for p in projection.uv_to_wgs84(uv)]).reshape(stop - start, f.width_px)
                result[start:stop] = (lat - zone.start_lat) * np.sign(zone.end_lat - zone.start_lat) > 0
            return result
        _, south, _, north = projection.wgs84_box()
        rows, cols = f.cardinal_rotation.geo_shape(f.height_px, f.width_px)
        lat = north - (np.arange(rows) + 0.5) / rows * (north - south)   # row centres from north to south
        towards_end: NDArray[Any] = (lat - zone.start_lat) * np.sign(zone.end_lat - zone.start_lat) > 0
        return np.asarray(f.cardinal_rotation.to_game(np.repeat(towards_end[:, None], cols, axis=1)), dtype=np.bool_)


class TransitionMaskParams(NodeParams):
    transition: Transition | None = None
    """A second look on part of the map (absent: one look, the mask is empty)."""


@node("climate.transition_mask", output="mask")
def transition_mask(frame: MapFrame, params: TransitionMaskParams, context: NodeContext) -> Mask:
    """The import layer of the transition: the area where the second look has begun; empty without a transition."""
    transition = params.transition
    if transition is None:
        return Mask(np.zeros((frame.height_px, frame.width_px), dtype=np.bool_), frame)
    values = ZoneMask(frame).beyond_start(transition.zone)
    context.log(f"transition area: {values.mean() * 100:.0f} % of the map; the climate mod lets the second look rise "
                f"over {transition.zone.real_length_m / 1000:.1f} km real; in the map editor import the file "
                f"_{transition.layer.key}.png for the layer of the same name")
    return Mask(values, frame)
