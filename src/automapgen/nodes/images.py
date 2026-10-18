"""Images: grey (8/16 bit) or RGB pictures for the game's import and for previews; made from rasters."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image as PilImage

from automapgen.graph import NodeParams, data_type, node
from automapgen.nodes.rasters import ClassMap, Mask


class PngCodec:
    suffix = ".png"

    def save(self, value: Image, path: Path) -> None:
        PilImage.fromarray(value.pixels).save(path)

    def load(self, path: Path) -> Image:
        with PilImage.open(path) as picture:
            return Image(np.asarray(picture))


@data_type("image", "A picture: grey (8 or 16 bit) or RGB.", codec=PngCodec(),
           preview=lambda i: PilImage.fromarray(i.pixels))
class Image:
    """Pixels rows x columns (grey) or rows x columns x 3 (RGB); read-only."""

    __slots__ = ("_pixels",)

    def __init__(self, pixels: NDArray[Any]) -> None:
        if pixels.dtype not in (np.uint8, np.uint16) or pixels.ndim not in (2, 3):
            raise ValueError(f"an image needs uint8/uint16 grey or RGB pixels, got {pixels.dtype} {pixels.shape}")
        pixels.setflags(write=False)
        self._pixels = pixels

    @property
    def pixels(self) -> NDArray[Any]:
        return self._pixels

    def png_bytes(self) -> bytes:
        buffer = io.BytesIO()
        PilImage.fromarray(self._pixels).save(buffer, format="PNG")
        return buffer.getvalue()


@node("image.from_mask", output="image")
def from_mask(mask: Mask) -> Image:
    """Mask -> grey image 0 / 255 (import layers of the game)."""
    return Image(mask.values.astype(np.uint8) * 255)


class PaletteParams(NodeParams):
    colors: dict[int, int | tuple[int, int, int]]
    """Grey value or RGB colour per class code; codes not listed become black. All grey or all RGB."""


@node("image.palette", output="image")
def palette(classes: ClassMap, params: PaletteParams) -> Image:
    """Class codes -> colours (e.g. biome ids -> the grey values the game reads)."""
    rgb = any(isinstance(c, tuple) for c in params.colors.values())
    table = np.zeros((256, 3) if rgb else 256, dtype=np.uint8)
    for code, color in params.colors.items():
        table[code] = color
    return Image(table[classes.values])


class TintParams(NodeParams):
    color: tuple[int, int, int]
    """Colour mixed into the masked pixels."""
    strength: float = 0.5
    """Share of the colour (0..1)."""


@node("image.tint", output="image")
def tint(image: Image, mask: Mask, params: TintParams) -> Image:
    """Mixes a colour into an RGB image where the mask is set (e.g. mountains in the biome preview)."""
    if image.pixels.ndim != 3 or image.pixels.shape[:2] != mask.values.shape:
        raise ValueError("tint needs an RGB image of the mask's size")
    rgb = image.pixels.copy()
    m = mask.values
    rgb[m] = (rgb[m] * (1 - params.strength) + np.array(params.color) * params.strength).astype(np.uint8)
    return Image(rgb)


_LANDCOVER_COLORS = {10: (0, 100, 0), 20: (255, 187, 34), 30: (255, 255, 76), 40: (240, 150, 255), 50: (250, 0, 0),
                     60: (180, 180, 180), 70: (240, 240, 240), 80: (0, 100, 200), 90: (0, 150, 160),
                     100: (250, 230, 160)}


class LandcoverPreviewParams(NodeParams):
    shore_class: int = 200
    """Own landcover class of the shore."""
    riverbed_class: int = 210
    """Own landcover class of the painted riverbed."""


@node("image.landcover", output="image")
def landcover_preview(classes: ClassMap, params: LandcoverPreviewParams) -> Image:
    """Landcover classes in the colours of ESA WorldCover (shore light brown, painted riverbed brown)."""
    table = np.zeros((256, 3), dtype=np.uint8)
    for code, rgb in {**_LANDCOVER_COLORS, params.shore_class: (200, 160, 110),
                      params.riverbed_class: (130, 110, 90)}.items():
        table[code] = rgb
    return Image(table[classes.values])
