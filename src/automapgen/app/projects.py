"""Projects: one map each, a parameter set `configs/projects/<id>.toml` (graph, the preset or landscape it extends,
the map's values, changed settings). The GUI reads and writes them; the CLI runs them (`--project <id>`)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from automapgen.app.toml_writer import TomlWriter
from automapgen.graph.paramsets import ParameterSet

_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
_KEYS = ("graph", "extends", "params", "defaults", "nodes")


@dataclass(frozen=True)
class ProjectEntry:
    id: str
    map_name: str
    graph: str


@dataclass(frozen=True)
class Look:
    """A preset or landscape a project can extend: path relative to the projects folder and display name."""

    path: str
    label: str


class ProjectStore:
    """The project files of a folder."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def entries(self) -> list[ProjectEntry]:
        out = []
        for path in sorted(self._folder.glob("*.toml")):
            project = ParameterSet.load(path)
            out.append(ProjectEntry(path.stem, str(project.params.get("map_name", path.stem)), project.graph or ""))
        return out

    def load(self, project_id: str) -> dict[str, Any]:
        """The project file as written (without what it extends): graph, extends, params, defaults, nodes."""
        data = tomllib.loads(self.path(project_id).read_text(encoding="utf-8"))
        return {key: data[key] for key in _KEYS if key in data}

    def merged(self, project_id: str) -> ParameterSet:
        """The project with everything it extends."""
        return ParameterSet.load(self.path(project_id))

    def parameters(self, document: dict[str, Any]) -> ParameterSet:
        """A project document not saved yet with everything it extends (relative to the projects folder)."""
        return ParameterSet.of(document, self._folder, "project")

    def save(self, project_id: str, document: dict[str, Any]) -> Path:
        """Writes the project file (comments of a hand-written file are lost) and checks that it can be read."""
        unknown = sorted(set(document) - set(_KEYS))
        if unknown:
            raise ValueError(f"unknown parts of a project: {unknown}")
        path = self.path(project_id)
        ordered = {key: document[key] for key in _KEYS if document.get(key) not in (None, {}, [])}
        name = ordered.get("params", {}).get("map_name", project_id)
        text = TomlWriter().write(ordered, f'Project "{name}" - written by the GUI (automapgen gui).')
        self._folder.mkdir(parents=True, exist_ok=True)
        previous = path.read_text(encoding="utf-8") if path.is_file() else None
        path.write_text(text, encoding="utf-8")
        try:
            ParameterSet.load(path)
        except Exception:
            if previous is None:
                path.unlink()
            else:
                path.write_text(previous, encoding="utf-8")
            raise
        return path

    def new_id(self, map_name: str) -> str:
        """File name of a new project for a map name."""
        return self.checked(map_name).lower()

    def path(self, project_id: str) -> Path:
        return self._folder / f"{self.checked(project_id)}.toml"

    def looks(self, configs: Path) -> list[Look]:
        """Presets and landscapes a project can extend."""
        out = []
        for kind in ("presets", "landscapes"):
            for path in sorted((configs / kind).glob("*.toml")):
                defaults = ParameterSet.load(path).defaults
                transition = defaults.get("transition") or {}
                name = transition.get("name") or defaults.get("climate", {}).get("name") or path.stem
                out.append(Look(f"../{kind}/{path.name}", f"{name} ({kind[:-1]} {path.stem})"))
        return out

    @staticmethod
    def checked(name: str) -> str:
        if not _ID.match(name or ""):
            raise ValueError(f"invalid project name {name!r}: letters, digits, '_' and '-' only")
        return name
