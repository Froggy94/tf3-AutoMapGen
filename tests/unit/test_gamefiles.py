"""Game files: folders and zipped folders below base/content; Lua data and node trees as values."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from automapgen.graph import NodeContext
from automapgen.lua import NodeTreeError
from automapgen.nodes import gamefiles
from automapgen.nodes.gamefiles import GameFileParams, GameFiles, LuaData, NodeTreeData

CLIMA = 'function data()\r\nreturn {\r\n  id = "temperate",   order = 1,\r\n}\r\nend\r\n'
TREE = 'function data() return { nodes = { { inputs = { a = { key = "out", nodeName = "%s" } }, name = "n" } } } end'


@pytest.fixture
def installation(tmp_path: Path) -> Path:
    content = tmp_path / "game" / "base" / "content"
    (content / "industries").mkdir(parents=True)
    (content / "industries" / "notes.lua").write_text("-- not an industry")
    with zipfile.ZipFile(content / "climates.zip", "w") as z:
        z.writestr("climates/temperate/temperate.clima.lua", "﻿" + CLIMA)
        z.writestr("climates/temperate/temperate_clima.tree.lua", TREE % "n")
    for name in ("mine", "farm"):
        with zipfile.ZipFile(content / "industries" / f"{name}.zip", "w") as z:
            z.writestr(f"{name}/{name}.con.lua", "x")
    return tmp_path / "game"


def test_files_are_read_from_folders_and_zipped_folders(installation: Path) -> None:
    files = GameFiles.at(installation)

    assert files.text("climates/temperate/temperate.clima.lua") == CLIMA.replace("\r\n", "\n")
    assert files.read("industries/farm/farm.con.lua") == b"x"
    assert files.read("industries/notes.lua") == b"-- not an industry"
    assert files.packages("industries") == ["farm", "mine"]
    with pytest.raises(FileNotFoundError, match=r"climates/dry/dry.clima.lua"):
        files.read("climates/dry/dry.clima.lua")


def test_a_local_copy_has_the_layout_of_base_content(tmp_path: Path) -> None:
    (tmp_path / "climates" / "temperate").mkdir(parents=True)
    (tmp_path / "climates" / "temperate" / "temperate.clima.lua").write_text(CLIMA)

    assert GameFiles.at(tmp_path).text("climates/temperate/temperate.clima.lua").startswith("function data()")


def test_lua_data_is_normalised_and_survives_its_codec(tmp_path: Path) -> None:
    data = LuaData(CLIMA)
    codec = gamefiles._LuaCodec(LuaData)

    codec.save(data, tmp_path / "x.lua")

    assert data.text == 'function data()\nreturn { \n\t\tid = "temperate",\n\t\torder = 1,\n\t}\nend\n'
    assert codec.load(tmp_path / "x.lua") == data
    document = data.document()
    document.root["id"] = "changed"
    assert data.document().root["id"] == "temperate"           # a value is never changed through its document


def test_node_trees_must_be_valid() -> None:
    assert [n.name for n in NodeTreeData(TREE % "n").tree()] == ["n"]
    with pytest.raises(NodeTreeError, match=r"n.a -> elsewhere"):
        NodeTreeData(TREE % "elsewhere")


def test_source_nodes_read_through_the_game_folder(installation: Path) -> None:
    context = NodeContext("game", print, lambda f: None, lambda: False, game=installation)

    tree = gamefiles.node_tree(GameFileParams(path="climates/temperate/temperate_clima.tree.lua"), context)
    clima = gamefiles.lua_file(GameFileParams(path="climates/temperate/temperate.clima.lua"), context)

    assert isinstance(tree, NodeTreeData)
    assert isinstance(tree, LuaData)
    assert clima.document().root["id"] == "temperate"
