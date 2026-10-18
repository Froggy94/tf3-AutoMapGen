"""Raster data types of the map: fields (real numbers), masks (yes/no) and class maps (codes), each with its frame.

A raster covers the whole map; its resolution follows from its size (the heightmap grid or a coarser work grid).
Values are read-only: nodes return new rasters instead of changing their inputs.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, Self

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from automapgen.graph import data_type
from automapgen.nodes.maps import MapFrame

type FloatArray = NDArray[np.float32]
type BoolArray = NDArray[np.bool_]
type CodeArray = NDArray[np.uint8]
type LabelArray = NDArray[np.int32]


class RasterCodec:
    """Stores a raster as .npz (values + frame)."""

    suffix = ".npz"

    def __init__(self, make: Callable[[Any, MapFrame], Any]) -> None:
        self._make = make

    def save(self, value: Any, path: Path) -> None:
        buffer = io.BytesIO()
        np.savez(buffer, values=value.values, frame=json.dumps(value.frame.to_json()))
        path.write_bytes(buffer.getvalue())

    def load(self, path: Path) -> Any:
        with np.load(path) as data:
            return self._make(data["values"], MapFrame.from_json(json.loads(str(data["frame"]))))


class Raster[A: np.ndarray[Any, Any]]:
    """Values over the whole map (read-only) and the map frame."""

    DTYPE: ClassVar[type[np.generic]]
    __slots__ = ("_frame", "_values")

    def __init__(self, values: NDArray[Any], frame: MapFrame) -> None:
        array = np.asarray(values, dtype=self.DTYPE)   # no copy (large maps); producers hand their array over
        array.setflags(write=False)
        self._values: A = array   # type: ignore[assignment]
        self._frame = frame

    @property
    def values(self) -> A:
        return self._values

    @property
    def frame(self) -> MapFrame:
        return self._frame

    @property
    def width(self) -> int:
        return int(self._values.shape[1])

    @property
    def height(self) -> int:
        return int(self._values.shape[0])

    @property
    def meters_per_pixel(self) -> tuple[float, float]:
        """Real metres per pixel along the map width and height."""
        return self._frame.meters_per_pixel(self.width, self.height)

    @property
    def pixel_m(self) -> float:
        """Mean real metres per pixel."""
        x, y = self.meters_per_pixel
        return (x + y) / 2

    def like(self, values: NDArray[Any]) -> Self:
        """A raster of the same kind with other values on the same frame."""
        return type(self)(values, self._frame)


@data_type("field", "Real numbers per pixel (heights in m, slopes in degrees, noise ...).",
           codec=RasterCodec(lambda values, frame: Field(values, frame)), preview=lambda f: f.preview())
class Field(Raster[FloatArray]):
    DTYPE = np.float32
    __slots__ = ()

    def preview(self) -> Image.Image:
        low, high = float(self.values.min()), float(self.values.max())
        scaled = (self.values - low) / (high - low) if high > low else np.zeros_like(self.values)
        return Image.fromarray(np.round(scaled * 255).astype(np.uint8))


@data_type("mask", "Yes/no per pixel (forest, water, mountains ...).",
           codec=RasterCodec(lambda values, frame: Mask(values, frame)),
           preview=lambda m: Image.fromarray(m.values.astype(np.uint8) * 255))
class Mask(Raster[BoolArray]):
    DTYPE = np.bool_
    __slots__ = ()


@data_type("classes", "A class code per pixel (landcover codes, biome ids ...).",
           codec=RasterCodec(lambda values, frame: ClassMap(values, frame)),
           preview=lambda c: Image.fromarray(c.values))
class ClassMap(Raster[CodeArray]):
    DTYPE = np.uint8
    __slots__ = ()


@data_type("labels", "An id per pixel (field groups, parcels ...); 0 = none.",
           codec=RasterCodec(lambda values, frame: Labels(values, frame)),
           preview=lambda c: Image.fromarray(((c.values.astype(np.int64) * 2654435761) % 251).astype(np.uint8)))
class Labels(Raster[LabelArray]):
    DTYPE = np.int32
    __slots__ = ()


def resize_nearest(values: NDArray[Any], width: int, height: int) -> NDArray[Any]:
    """Integer raster (masks as 0/255, codes) resized with nearest neighbour (Pillow)."""
    return np.array(Image.fromarray(values).resize((width, height), Image.Resampling.NEAREST))


def resize_floats(values: NDArray[Any], width: int, height: int, method: Image.Resampling) -> FloatArray:
    """Float raster resized with a Pillow method."""
    image = Image.fromarray(values.astype(np.float32), mode="F")
    return np.array(image.resize((width, height), method), dtype=np.float32)
