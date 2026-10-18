"""Settings of the GUI (configs/gui.toml) and the folders it works with."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from automapgen.app.steps import ProcessingStep


class _Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class DemType(_Value):
    key: str
    """Elevation model of the OpenTopography API."""
    label: str
    """Display name."""


class GraphChoice(_Value):
    id: str
    """Graph id (`<id>.graph.json`)."""
    label: str
    """Display name in the graph list."""


class SettingsSection(_Value):
    key: str
    """Identifier (stays the same when the label is translated)."""
    label: str
    """Display name."""
    types: tuple[str, ...] = ()
    """Node types whose parameters belong here (patterns: "rivers.*"); the first section that fits wins."""


class SettingsLayout(_Value):
    """What the advanced settings list and how they are grouped. Every parameter of the project's graph is listed
    unless it is hidden here, so a new node parameter appears without a change of the GUI."""

    main_page: tuple[str, ...] = ()
    """Exposed parameters of the graph the main page edits (not listed)."""
    hidden: tuple[str, ...] = ()
    """Node parameters not listed: patterns of "<node type>.<parameter>" ("file.*": the file names)."""
    sections: tuple[SettingsSection, ...] = ()
    """Sections in display order."""
    other: SettingsSection = SettingsSection(key="other", label="Other")
    """Section of parameters no section takes."""


class GuiSettings(_Value):
    port: int = 8765
    """Local port (only http://127.0.0.1)."""
    open_browser: bool = True
    """Open the page in the browser on start."""
    scale_steps: tuple[float, ...] = (1, 2, 2.5, 3, 4, 5, 10)
    """Scale steps (1:x) the slider snaps to."""
    large_map_km2: float = 400
    """Warn about memory from this game area on."""
    default_landscape: str = "../presets/alpine_realistic.toml"
    """What a new project extends (relative to the projects folder)."""
    dem_types: tuple[DemType, ...] = ()
    """Elevation models to choose from (first = default)."""
    graphs: tuple[GraphChoice, ...] = ()
    """Graphs a project can use (graph list), first = default."""
    steps: tuple[ProcessingStep, ...] = ()
    """Processing steps of a run (tick boxes), in display order."""
    advanced: SettingsLayout = SettingsLayout()
    """The advanced settings."""

    @classmethod
    def load(cls, path: Path) -> GuiSettings:
        return cls.model_validate(tomllib.loads(path.read_text(encoding="utf-8"))) if path.is_file() else cls()


@dataclass(frozen=True)
class GuiPaths:
    """Folders and files of the GUI below the working directory `root`."""

    root: Path

    @property
    def configs(self) -> Path:
        return self.root / "configs"

    @property
    def projects(self) -> Path:
        return self.configs / "projects"

    @property
    def graphs(self) -> Path:
        """Own graphs, searched before the built-in ones."""
        return self.configs / "graphs"

    @property
    def output(self) -> Path:
        return self.root / "output"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def downloads(self) -> Path:
        return self.root / "downloads"

    @property
    def local_settings(self) -> Path:
        return self.root / "local" / "settings.toml"

    @property
    def state(self) -> Path:
        """Last project and map view (this computer only)."""
        return self.root / "local" / "gui_state.json"
