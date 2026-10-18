"""A quick look at an elevation file for the GUI: extent and height range (whole or within a crop), read coarsely."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

_IMPLAUSIBLE_M = 1e5
_PREVIEW_PX = 1500


@dataclass(frozen=True)
class TifInfo:
    bounds: tuple[float, float, float, float]
    """(west, south, east, north) in WGS84 degrees."""
    min_m: float | None
    max_m: float | None

    def as_json(self) -> dict[str, Any]:
        return {"bounds": list(self.bounds), "min": self.min_m, "max": self.max_m}

    @classmethod
    def read(cls, path: Path, crop: tuple[float, float, float, float] | None = None) -> TifInfo:
        """`crop`: (west, south, east, north) in WGS84 degrees."""
        with rasterio.open(path) as src:
            lon0, lat0, lon1, lat1 = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
            window = None
            if crop is not None:
                left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, *crop)
                window = from_bounds(left, bottom, right, top, src.transform).round_offsets().round_lengths()
                window = window.intersection(Window(0, 0, src.width, src.height))
            w = window.width if window is not None else src.width
            h = window.height if window is not None else src.height
            step = max(1, int(max(w, h) / _PREVIEW_PX))
            a = src.read(1, window=window, out_shape=(max(1, int(h / step)), max(1, int(w / step))), masked=True)
        heights = a.astype(np.float64).filled(np.nan)
        heights[np.abs(heights) > _IMPLAUSIBLE_M] = np.nan
        ok = np.isfinite(heights)
        return cls((lon0, lat0, lon1, lat1), float(np.nanmin(heights)) if ok.any() else None,
                   float(np.nanmax(heights)) if ok.any() else None)
