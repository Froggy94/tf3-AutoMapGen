"""What the game's import dialog needs after a run: climate mod, heights, import files, towns file, previews."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from automapgen.graph.paramsets import ParameterSet

MIN_HEIGHT_RAISE_GAME_M = 3.0
"""Added to the computed minimum height of the import dialog: with the exact value the water lies too high in the game
(user, tested in game 2026-10-18; docsAI/notes/tf3_facts.md)."""

_PREVIEWS = ("_biome_preview.png", "_landcover_preview.png", "_crops_preview.png", "_rivers_preview.png",
             "_rivers_valley_preview.png", "_towns_preview.png", "_industries_preview.png", "_paths_preview.png")


class ImportSummary:
    """Built from the output folder of a map and its merged parameters."""

    def __init__(self, output: Path) -> None:
        self._output = output

    def build(self, parameters: ParameterSet) -> dict[str, Any]:
        params, defaults = parameters.params, parameters.defaults
        name = str(params["map_name"])
        folder = self._output / name
        height_scale = params.get("height_scale", defaults.get("height_scale"))
        if height_scale is None:
            height_scale = params.get("scale", defaults.get("scale", 1))
        height_scale = float(height_scale)
        heights = self._heights(folder / f"{name}_height_range.json")
        files = [("Heightmap", f"{name}.png"), ("Biomes", f"{name}_biomes.png")]
        files += [(key, f"{name}_{key}.png") for key in sorted(self._layers(defaults))]
        towns = next((f"{name}{suffix}" for suffix in ("_industries.lua", "_towns.lua")
                      if (folder / f"{name}{suffix}").is_file()), None)
        mods = folder / "mods"
        return {
            "map": name,
            "mods": sorted(p.name for p in mods.iterdir()) if mods.is_dir() else [],
            "min_game": None if heights is None
            else (heights["black_m"] - heights["min_height_m"]) / height_scale + MIN_HEIGHT_RAISE_GAME_M,
            "min_raise_game": MIN_HEIGHT_RAISE_GAME_M,
            "max_game": None if heights is None else (heights["max_height_m"] - heights["min_height_m"]) / height_scale,
            "max_real": None if heights is None else heights["max_height_m"],
            "files": [(label, file, (folder / file).is_file()) for label, file in files],
            "towns_file": towns,
            "previews": [f"/output/{name}/{name}{p}" for p in _PREVIEWS if (folder / f"{name}{p}").is_file()],
        }

    @staticmethod
    def _layers(defaults: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
        """The import layers of the climate (and the transition mask), by key."""
        layers = dict(defaults.get("climate", {}).get("slots", {}).get("layers", {}))
        transition = defaults.get("transition")
        if transition and transition.get("enabled", True):
            layers[transition["layer"]["key"]] = transition["layer"]
        return layers

    @staticmethod
    def _heights(path: Path) -> dict[str, float] | None:
        if not path.is_file():
            return None
        data: dict[str, float] = json.loads(path.read_text(encoding="utf-8"))
        return data
