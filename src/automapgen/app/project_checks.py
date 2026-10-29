"""Checks of a project that the graph compiler cannot make: game material names (they need the game installation)
and the name of a climate whose look a project changes (the climate mod is shared by name)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from automapgen.graph.errors import Problem
from automapgen.graph.plan import ExecutionPlan
from automapgen.nodes.climate.preset import ClimatePreset, Landscape, Transition
from automapgen.nodes.gamefiles import GameFiles

MATERIALS_FOLDER = "terrain/materials"            # below base/content: one zip per terrain material
_NOT_A_MATERIAL = "shared"                         # shared textures of the materials
_LOOKS = ("climate", "transition")                 # [defaults] tables that make the climate mod's look


class ProjectChecks:
    """Problems and warnings of a project beyond the compiler. `game_folder` gives the game installation (None: not
    set, material names are not checked)."""

    def __init__(self, game_folder: Callable[[], Path | None]) -> None:
        self._game_folder = game_folder

    def problems(self, plan: ExecutionPlan) -> list[Problem]:
        """Game materials the climate presets of the plan use but the game does not have."""
        known = self._known_materials()
        if not known:
            return []
        used: set[str] = set()
        for node in plan.nodes:
            climate, transition = getattr(node.params, "climate", None), getattr(node.params, "transition", None)
            if isinstance(climate, ClimatePreset):
                try:
                    used |= Landscape(climate, transition if isinstance(transition, Transition) else None) \
                        .game_materials()
                except ValueError:
                    continue      # a broken transition is reported by its node when it runs
        return [Problem("climate preset", f"unknown game material '{name}' (not in the game's "
                                          f"base/content/{MATERIALS_FOLDER}; list: docsAI/reference/README.md)")
                for name in sorted(used - known)]

    @staticmethod
    def warnings(project: Mapping[str, Any], merged: Mapping[str, Any]) -> list[str]:
        """A project file (`project`: its own values) that changes the look but keeps the name of its preset builds a
        climate mod of the same name as the other maps of that preset (same rounded reference height, scale and height
        scale): one overwrites the other. The mod is named by the transition if the merged `[defaults]` (`merged`)
        have one."""
        own = project.get("defaults", {})
        transition = merged.get("transition")
        named = "transition" if isinstance(transition, Mapping) and transition.get("enabled", True) else "climate"
        changed = any(isinstance(look := own.get(key), Mapping) and set(look) - {"name"} for key in _LOOKS)
        if not changed or "name" in own.get(named, {}):
            return []
        return [f"the project changes the look of the climate mod but not its name: the mod gets the same name as "
                f"that of other maps with this preset and overwrites it - set an own [defaults.{named}] name"]

    def _known_materials(self) -> set[str]:
        folder = self._game_folder()
        if folder is None:
            return set()
        return set(GameFiles.at(folder).packages(MATERIALS_FOLDER)) - {_NOT_A_MATERIAL}
