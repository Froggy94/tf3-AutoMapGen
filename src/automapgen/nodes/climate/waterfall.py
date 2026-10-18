"""Waterfall paint brushes: the game's water_dirty colour with white foam, seamless in every direction."""

from __future__ import annotations

import io
import math
import struct
from typing import Any, Literal, NamedTuple

import numpy as np
from numpy.typing import NDArray
from PIL import Image as PilImage
from PIL import ImageDraw
from pydantic import BaseModel, ConfigDict
from scipy import ndimage

from automapgen.graph import NodeContext, NodeParams, node
from automapgen.nodes.climate.preset import Named, Optional
from automapgen.nodes.gamefiles import GameFiles
from automapgen.nodes.images import Image
from automapgen.nodes.mods import CONTENT, ModFiles

type FloatImage = NDArray[np.floating[Any]]

FOLDER = "dhe_waterfall"
_WATER = "terrain/materials/water_dirty/tex/water_dirty"
_GRAVEL_GLOSS = "terrain/materials/gravel_01/tex/gravel_01_metal_gloss_ao_h.dds"
_NORMAL_MAP = "::/terrain/materials/water_dirty/tex/water_dirty_nrml.dds"


class _Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class WaterfallVariant(_Value):
    """One brush (the key in `variants` is its label: name suffix of the brush, dhe_waterfall_<label>)."""

    foam_share: float
    """About the foam share of the area (0..1)."""
    strength: float
    """Foam strength (1 = full white)."""
    grain_weight: float
    """Share of the fine spray in the pattern (0..1, rest = clumps)."""
    pattern: Literal["-", "h", "v", "img", "img90", "imgmix"]
    """Pattern: "-" noise, "h"/"v" waves, "img"/"img90"/"imgmix" photo."""


class WaterfallSettings(_Value):
    """Waterfall brush textures: water_dirty colour with white foam, seamless in all directions (a value of
    configs/waterfall.toml)."""

    name: str
    """Brush name in game (German via the translations)."""
    size_px: int
    """Texture size (original 4096; 2048 = about 5.6 MB instead of 22 MB per brush)."""
    foam_color: tuple[int, int, int]
    """Foam colour sRGB."""
    softness: float
    """Transition water -> foam (larger = softer)."""
    clump_px: float
    """Size of the foam clumps in texture pixels (4096 px = 32 m)."""
    grain_px: float
    """Size of the fine spray in texture pixels."""
    foam_gloss: float
    """Gloss factor in the foam (1 = like water, 0 = matt)."""
    seed: int
    """Random seed."""
    variants: Named[WaterfallVariant]
    """Brushes by label (one per variant, about 5.6 MB each)."""
    image: str
    """Photo for the photo variants (relative to the working directory)."""
    image_highpass_px: float
    """Background smoothing of the photo (image pixels)."""
    wave_px: int
    """Distance of the wave crests in texture pixels (4096 px = 32 m)."""
    wave_warp: float
    """Bending of the wave crests (in wave distances)."""
    wave_warp_scale: float
    """Size of the bending (in wave distances)."""
    first_order: int
    """Brush order in the game menu of the first variant."""


def periodic_noise(n: int, size_px: float, rng: np.random.Generator) -> FloatImage:
    """Seamless, direction-free n x n noise: white noise low-pass filtered in frequency space (the FFT is periodic),
    returned as ranks 0..1 (uniformly distributed)."""
    f = np.fft.fftfreq(n)
    r = np.hypot(f[:, None], f[None, :]) * size_px
    spectrum = np.fft.fft2(rng.standard_normal((n, n))) * np.exp(-0.5 * r * r)
    return _ranks(np.fft.ifft2(spectrum).real)


def _ranks(a: FloatImage) -> FloatImage:
    n = a.shape[0]
    result: FloatImage = (np.argsort(np.argsort(a, axis=None)).reshape(n, n) + 0.5) / a.size
    return result


class DdsEncoder:
    """DXT1/DXT5 with all mipmaps; header taken from a game texture of the same format, size patched."""

    def __init__(self, size: int) -> None:
        self._n = size
        self._mips = int(math.log2(size)) + 1

    def encode(self, image: PilImage.Image, header: bytes, pixel_format: str = "DXT1") -> bytes:
        level, parts = image, []
        for _ in range(self._mips):
            buffer = io.BytesIO()
            level.save(buffer, "DDS", pixel_format=pixel_format)
            parts.append(buffer.getvalue()[128:])
            level = level.resize((max(1, level.width // 2), max(1, level.height // 2)), PilImage.Resampling.BOX)
        head = bytearray(header[:128])
        block = 16 if pixel_format == "DXT5" else 8
        struct.pack_into("<3I", head, 12, self._n, self._n, max(1, self._n // 4) ** 2 * block)
        struct.pack_into("<I", head, 28, self._mips)
        return bytes(head) + b"".join(parts)


def material_file(label: str) -> str:
    """File name (without .tmat) of the waterfall brush with this label."""
    return f"{FOLDER}_{label.lower()}" if label else FOLDER


class _GameTextures(NamedTuple):
    albedo: bytes
    mgah: bytes
    dxt5_header: bytes


class WaterfallBrushes:
    """The waterfall brushes (`.tmat`, colour and gloss DDS, icons) and a preview of 2 x 2 tiles per brush."""

    def __init__(self, settings: WaterfallSettings, textures: _GameTextures, photo: PilImage.Image) -> None:
        self._p = settings
        self._textures = textures
        self._photo_image = photo

    @staticmethod
    def textures(files: GameFiles) -> _GameTextures:
        """The game textures the brushes are made from (checked: a game update may change their format)."""
        albedo, mgah = files.read(f"{_WATER}_albedo.dds"), files.read(f"{_WATER}_metal_gloss_ao_h.dds")
        for data in (albedo, mgah):
            if data[84:88] != b"DXT1":
                raise ValueError(f"water_dirty: texture format {data[84:88]!r} instead of DXT1 - game update?")
        dxt5_header = files.read(_GRAVEL_GLOSS)[:128]
        if dxt5_header[84:88] != b"DXT5":
            raise ValueError("gravel_01: gloss texture is not DXT5 - game update?")
        return _GameTextures(albedo, mgah, dxt5_header)

    def build(self, log: Any) -> tuple[dict[str, bytes], PilImage.Image]:
        p, albedo, mgah = self._p, self._textures.albedo, self._textures.mgah
        out = f"{CONTENT}terrain/materials/{FOLDER}/"
        n = p.size_px
        source_n = struct.unpack("<I", albedo[16:20])[0]
        k = source_n / n                                  # original pixels per texture pixel
        rng = np.random.default_rng(p.seed)
        clump = periodic_noise(n, p.clump_px / k / 2.5, rng)
        grain = periodic_noise(n, p.grain_px / k / 2.5, rng)
        water_rgb = np.asarray(PilImage.open(io.BytesIO(albedo)).convert("RGB"), np.float32).reshape(-1, 3).mean(
            axis=0)
        gloss = np.asarray(PilImage.open(io.BytesIO(mgah)).convert("RGBA").resize(
            (n, n), PilImage.Resampling.BOX)).astype(np.float32)
        shapes = self._shapes(n, source_n, k, rng)
        encoder = DdsEncoder(n)

        files: dict[str, bytes] = {}
        tiles: list[tuple[str, PilImage.Image]] = []
        for i, (label, variant) in enumerate(p.variants.items()):
            name = material_file(label)
            pattern = (1 - variant.grain_weight) * shapes.get(variant.pattern, clump) + variant.grain_weight * grain
            t = np.quantile(pattern, 1 - variant.foam_share)
            foam = np.clip((pattern - t) / max(p.softness * pattern.std() * 4, 1e-6) + 0.5, 0, 1)
            mask = foam * foam * (3 - 2 * foam)               # soft edge (smoothstep)
            foam = mask * variant.strength

            rgb = water_rgb + (np.array(p.foam_color, np.float32) - water_rgb) * foam[..., None]
            tile = PilImage.fromarray(np.clip(np.rint(rgb), 0, 255).astype(np.uint8), "RGB")
            files[f"{out}tex/{name}_albedo.dds"] = encoder.encode(tile.convert("RGBA"), albedo)

            a = gloss.copy()
            a[..., 1] *= 1 - (1 - p.foam_gloss) * foam        # gloss of the original waves, damped in the foam
            height = variant.pattern.startswith("img")        # height channel: foam up, water down (DXT5)
            if height:
                a[..., 3] = 255 * mask
            gloss_image = PilImage.fromarray(np.clip(np.rint(a), 0, 255).astype(np.uint8), "RGBA")
            files[f"{out}tex/{name}_mgah.dds"] = encoder.encode(
                gloss_image, self._textures.dxt5_header if height else mgah, "DXT5" if height else "DXT1")

            crop = tile.crop((0, 0, n // 4, n // 4))
            files[f"{out}{name}@2x.tga"] = _tga(crop.resize((64, 64), PilImage.Resampling.LANCZOS))
            files[f"{out}{name}_preview@2x.tga"] = _tga(crop.resize((256, 256), PilImage.Resampling.LANCZOS))
            files[f"{out}{name}.tmat.lua"] = self._tmat(name, label, p.first_order + i).encode("utf-8")
            log(f"waterfall brush {label}: {name} ({n} px, foam area {variant.foam_share * 100:.0f} %, strength "
                f"{variant.strength * 100:.0f} %, white on average {float(foam.mean()) * 100:.0f} %, pattern "
                f"{variant.pattern})")
            tiles.append((label, tile))
        return files, self._preview(tiles, n)

    def _shapes(self, n: int, source_n: int, k: float, rng: np.random.Generator) -> dict[str, FloatImage]:
        """Wave patterns (sine with whole periods per tile, phase warped by periodic noise) and the photo patterns."""
        p = self._p
        waves = max(1, round(source_n / p.wave_px))
        warp = (periodic_noise(n, p.wave_px * p.wave_warp_scale / k / 2.5, rng) - 0.5) * 2 * p.wave_warp
        coord = np.arange(n, dtype=np.float32) / n
        photo = self._photo(n)
        photo90 = np.rot90(photo).copy()
        mix = _ranks(np.maximum(photo, photo90))              # foam where either image is white
        return {"h": 0.5 + 0.5 * np.sin(2 * np.pi * (waves * coord[:, None] + warp)),
                "v": 0.5 + 0.5 * np.sin(2 * np.pi * (waves * coord[None, :] + warp)),
                "img": photo, "img90": photo90, "imgmix": mix}

    def _photo(self, n: int) -> FloatImage:
        """White parts of the photo (high-pass), centre square, made seamless by blending 4 shifted copies."""
        photo = np.asarray(self._photo_image.convert("L"), np.float32)
        m = min(photo.shape)
        y0, x0 = (photo.shape[0] - m) // 2, (photo.shape[1] - m) // 2
        photo = photo - ndimage.gaussian_filter(photo, self._p.image_highpass_px)
        photo = photo[y0:y0 + m, x0:x0 + m]
        w = np.sin(np.pi * (np.arange(m) + 0.5) / m) ** 2
        wy, wx = w[:, None], w[None, :]
        h2 = m // 2
        parts = [(photo, wy * wx), (np.roll(photo, h2, 1), wy * (1 - wx)), (np.roll(photo, h2, 0), (1 - wy) * wx),
                 (np.roll(photo, (h2, h2), (0, 1)), (1 - wy) * (1 - wx))]
        blended = sum(img * weight for img, weight in parts) / np.sqrt(sum(weight * weight for _, weight in parts))
        resized = np.asarray(PilImage.fromarray(blended).resize((n, n), PilImage.Resampling.BICUBIC))
        return _ranks(resized)

    def _tmat(self, name: str, label: str, order: int) -> str:
        """The material file in the layout of the game's materials (a template, not the Lua writer)."""
        title = f"{self._p.name} {label}".strip()
        return f"""function data()
return {{
	name = _("{title}"),

	icon = "{name}.tga",
	previewIcon = "{name}_preview.tga",
	detailColorTexture = "tex/{name}_albedo.dds",
	detailMetalGlossAoHTexture = "tex/{name}_mgah.dds",
	detailNormalTexture = "{_NORMAL_MAP}",
	overlayTexture = "::/terrain/materials/shared/tex/overlay_0.dds",
	desc = _("Paint the ground with the materials brush."),

	detailSize = 1 / 32,
	overlaySize = 0.12,
	overlayStrength = 0.0,

	categories = {{"misc"}},
	order = {order},
	priority = 1000000,
	categoryList = {{
		categories = {{ "all.clima", }}
	}},
}}
end
"""

    def _preview(self, tiles: list[tuple[str, PilImage.Image]], n: int) -> PilImage.Image:
        """2 x 2 tiles per brush side by side (to check seams), labelled."""
        cell = 512
        preview = PilImage.new("RGB", (cell * len(tiles), cell + 40), (255, 255, 255))
        draw = ImageDraw.Draw(preview)
        for j, (label, tile) in enumerate(tiles):
            quad = PilImage.new("RGB", (2 * n, 2 * n))
            for dx in (0, n):
                for dy in (0, n):
                    quad.paste(tile, (dx, dy))
            preview.paste(quad.resize((cell, cell), PilImage.Resampling.LANCZOS), (j * cell, 40))
            draw.text((j * cell + 10, 10), f"{self._p.name} {label}", fill=(0, 0, 0), font_size=24)
        return preview


def _tga(image: PilImage.Image) -> bytes:
    """Uncompressed TGA like the game's material icons."""
    buffer = io.BytesIO()
    image.save(buffer, format="TGA")
    return buffer.getvalue()


class WaterfallParams(NodeParams):
    waterfall: Optional[WaterfallSettings] = None
    """Waterfall brushes (absent: none)."""


class Waterfall(NamedTuple):
    files: ModFiles
    preview: Image


@node("climate.waterfall")
def waterfall(params: WaterfallParams, context: NodeContext) -> Waterfall:
    """The waterfall brushes of the climate mod (made from the game's water texture and a photo) and a preview of
    2 x 2 tiles per brush to check the seams."""
    if params.waterfall is None:
        return Waterfall(ModFiles(()), Image(np.full((1, 1, 3), 255, dtype=np.uint8)))
    textures = WaterfallBrushes.textures(GameFiles.at(context.game_folder()))
    with PilImage.open(params.waterfall.image) as photo:
        files, preview = WaterfallBrushes(params.waterfall, textures, photo).build(context.log)
    return Waterfall(ModFiles.of(files), Image(np.asarray(preview)))
