"""The game's map sizes and aspect ratios (configs/map_sizes.toml): tiles per size and format."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator

PIXELS_PER_TILE = 64          # one tile = 256 game metres = 64 heightmap pixels of 4 m


class _Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class MapSize(_Value):
    key: str
    """Key of the size."""
    label: str
    """Name in the game."""
    tiles: tuple[tuple[int, int], ...]
    """(width, height) in tiles per format."""


class MapSizeCatalog(_Value):
    tile_m: int
    """Game metres per tile."""
    formats: tuple[str, ...]
    """Format names in the game, e.g. "1x2" (width : height)."""
    sizes: tuple[MapSize, ...]
    """The sizes, smallest first."""

    @model_validator(mode="after")
    def _one_tile_count_per_format(self) -> MapSizeCatalog:
        wrong = [s.key for s in self.sizes if len(s.tiles) != len(self.formats)]
        if wrong:
            raise ValueError(f"sizes {wrong} need one tile count per format {list(self.formats)}")
        return self

    @classmethod
    def load(cls, path: Path) -> MapSizeCatalog:
        return cls.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))

    def game_km(self, size: str, map_format: str) -> tuple[float, float]:
        """Width and height of the game map in km."""
        tiles = self._tiles(size, map_format)
        return tiles[0] * self.tile_m / 1000, tiles[1] * self.tile_m / 1000

    def find(self, width_km: float, height_km: float) -> tuple[str, str] | None:
        """(size, format) of a game map of this extent, None if it is no size of the game."""
        for s in self.sizes:
            for f, (tw, th) in zip(self.formats, s.tiles, strict=True):
                if abs(tw * self.tile_m / 1000 - width_km) < 1e-6 and abs(th * self.tile_m / 1000 - height_km) < 1e-6:
                    return s.key, f
        return None

    def _tiles(self, size: str, map_format: str) -> tuple[int, int]:
        found = next((s for s in self.sizes if s.key == size), None)
        if found is None or map_format not in self.formats:
            raise ValueError(f"unknown map size {size!r} or format {map_format!r}")
        return found.tiles[self.formats.index(map_format)]
