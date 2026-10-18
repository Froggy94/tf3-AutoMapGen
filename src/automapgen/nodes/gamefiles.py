"""The game's files: read access (installation or a local copy, ADR 0005), Lua data files and node trees as values,
and the player's user data folder that receives the results.

Paths name files below the game's `base/content`, e.g. `climates/temperate/temperate_clima.tree.lua`. The game ships
most folders zipped (`climates.zip` holds `climates/...`, `terrain/materials/water_dirty.zip` holds `water_dirty/...`);
`GameFiles` looks into folders and zipped folders alike, so a run setting decides whether the installation or an
extracted copy is read.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from automapgen.graph import NodeContext, NodeParams, data_type, node
from automapgen.lua import DataDocument, NodeTree


class GameFiles:
    """Files below the game's `base/content`: plain files first, then the zipped folder that holds the path."""

    def __init__(self, content: Path) -> None:
        self._content = content

    @classmethod
    def at(cls, folder: Path) -> GameFiles:
        """The game installation (with `base/content`) or a local copy laid out like `base/content`."""
        content = folder / "base" / "content"
        return cls(content if content.is_dir() else folder)

    def read(self, path: str) -> bytes:
        file = self._content / path
        if file.is_file():
            return file.read_bytes()
        parts = path.split("/")
        for i in range(len(parts) - 1, 0, -1):
            archive = self._content.joinpath(*parts[:i - 1]) / f"{parts[i - 1]}.zip"
            if archive.is_file():
                with zipfile.ZipFile(archive) as z:
                    try:
                        return z.read("/".join(parts[i - 1:]))
                    except KeyError:
                        break
        raise FileNotFoundError(f"game file {path!r} not found below {self._content}")

    def text(self, path: str) -> str:
        """A text file with Unix line ends, without byte order mark."""
        return self.read(path).decode("utf-8-sig").replace("\r\n", "\n")

    def packages(self, folder: str) -> list[str]:
        """Names of the zipped folders in a folder, e.g. the industries."""
        return sorted(p.stem for p in (self._content / folder).glob("*.zip"))


class GameUserData:
    """The game's user data folder of a player (`userdata/<id>/<game id>/local`): the map editor imports heightmaps
    from `heightmaps/`, layers from `biomes/` and towns/industries files from `towns_industries/`; the game offers
    the mods in `staging_area/` to activate. The folders are named destinations of a run (sink parameter
    `copy_to`)."""

    FOLDERS = ("heightmaps", "biomes", "towns_industries", "staging_area")

    def __init__(self, local: Path) -> None:
        self._local = local

    def destinations(self) -> dict[str, Path]:
        return {name: self._local / name for name in self.FOLDERS}


class _LuaCodec:
    suffix = ".lua"

    def __init__(self, make: Callable[[str], Any]) -> None:
        self._make = make

    def save(self, value: LuaData, path: Path) -> None:
        path.write_text(value.text, encoding="utf-8", newline="\n")

    def load(self, path: Path) -> Any:
        return self._make(path.read_text(encoding="utf-8"))


@data_type("lua_data", "A Lua data file of the game (`function data() return {...} end`).",
           codec=_LuaCodec(lambda text: LuaData(text)), preview=lambda d: d.text[:4000])
@dataclass(frozen=True)
class LuaData:
    """The file's text in the layout of the game's node editor - normalised on creation, so a value is the same fresh
    and after the cache. Nodes edit a copy: `document()`."""

    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", DataDocument.parse(self.text).to_lua())

    @classmethod
    def of(cls, document: DataDocument) -> Self:
        return cls(document.to_lua())

    def document(self) -> DataDocument:
        """A new, editable document of this data."""
        return DataDocument.parse(self.text)


@data_type("node_tree", "A node tree of the game (`*.tree.lua`): a terrain generator, e.g. a climate's material tree.",
           codec=_LuaCodec(lambda text: NodeTreeData(text)),
           preview=lambda t: f"{sum(1 for _ in t.tree())} nodes")
@dataclass(frozen=True)
class NodeTreeData(LuaData):
    """A Lua data file whose nodes form a valid tree (every input points to an existing node)."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.tree().validate()

    def tree(self) -> NodeTree:
        """A new, editable tree of this data."""
        return NodeTree(self.document())


class GameFileParams(NodeParams):
    path: str = ""
    """File below the game's base/content, e.g. climates/temperate/temperate.clima.lua."""


@node("game.lua_file", output="data")
def lua_file(params: GameFileParams, context: NodeContext) -> LuaData:
    """A Lua data file of the game."""
    return LuaData(GameFiles.at(context.game_folder()).text(params.path))


@node("game.node_tree", output="tree")
def node_tree(params: GameFileParams, context: NodeContext) -> NodeTreeData:
    """A node tree of the game."""
    return NodeTreeData(GameFiles.at(context.game_folder()).text(params.path))
