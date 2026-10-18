"""Lua data files: parser, writer layout, node trees and their structural signature."""

from __future__ import annotations

import pytest

from automapgen.lua import (
    DataDocument,
    LuaCall,
    LuaExpression,
    LuaNumber,
    LuaSyntaxError,
    LuaTable,
    NodeTree,
    NodeTreeError,
    Port,
    TreeNode,
    TreeSignature,
    to_plain,
)

SAMPLE = '''﻿function data()
return {
\t-- a comment
\tid = "temperate",
\tdesc = { name = _("Temperate"), },
\tscale = 1 / 32,
\tlevels = { 0, 1.50, -2e3, },
\t["odd key"] = true,
\t[3] = nil,
\ttext = [[two
lines]],
\t--[[ block
\tcomment ]]
}
end
'''

TREE = '''function data()
return {
\tnodes = {
\t\t{ color = { 1, 1, 1, 1, }, inputs = { }, layerType = "HEIGHT", name = "height", params = { },
\t\t  position = { 0, 0, }, },
\t\t{ color = { 1, 1, 1, 1, }, inputs = { input = { key = "out", nodeName = "height", }, },
\t\t  layerType = "CURVE", name = "curve", params = { points = { { 0, 0, }, { 1, 1, }, }, },
\t\t  position = { 100, 0, }, },
\t},
\tpreviewSeed = 7,
}
end
'''


def test_parse_keeps_numbers_calls_and_expressions_as_written() -> None:
    root = DataDocument.parse(SAMPLE).root

    assert root["id"] == "temperate"
    assert root.table("desc")["name"] == LuaCall("_", ("Temperate",))
    assert root["scale"] == LuaExpression("1 / 32")
    assert LuaExpression("1 / 32").value == 1 / 32
    assert root.table("levels").array() == [LuaNumber("0"), LuaNumber("1.50"), LuaNumber("-2e3")]
    assert root["odd key"] is True
    assert root["text"] == "two\nlines"


def test_written_text_parses_to_the_same_document() -> None:
    document = DataDocument.parse(SAMPLE)

    text = document.to_lua()

    assert text.startswith("function data()\nreturn { \n\t\tid = \"temperate\",\n")
    assert "levels = { 0, 1.50, -2e3, }," in text
    assert DataDocument.parse(text).root == document.root
    assert DataDocument.parse(text).to_lua() == text


def test_syntax_errors_name_the_line() -> None:
    with pytest.raises(LuaSyntaxError, match="line 2"):
        DataDocument.parse("function data()\nreturn { a = = 1 }\nend")


def test_table_editing_keeps_the_order() -> None:
    table = LuaTable([("a", LuaNumber("1")), (None, "x"), ("b", "y")])

    table["a"] = "changed"
    table.insert_array(0, "first")
    table.remove("b")

    assert list(table.entries()) == [("a", "changed"), (None, "first"), (None, "x")]
    assert to_plain(table) == {"[array]": ["first", "x"], "a": "changed"}


def test_node_tree_rewires_sets_parameters_and_validates() -> None:
    tree = NodeTree(DataDocument.parse(TREE))

    tree.add(TreeNode.create("scale", "MUL", {"a": Port("curve")}, {"factor": 0.5}))
    tree.node("curve").set_param("points", [(0, 0), (1, 0.5)])
    tree.validate()

    assert [n.name for n in tree] == ["height", "curve", "scale"]
    assert tree.node("scale").inputs() == {"a": Port("curve", "out")}
    with pytest.raises(NodeTreeError, match="no parameter"):
        tree.node("curve").set_param("missing", 1)
    with pytest.raises(NodeTreeError, match="duplicate"):
        tree.add(TreeNode.create("scale", "MUL"))
    tree.node("scale").set_input("a", Port("nowhere"))
    with pytest.raises(NodeTreeError, match=r"scale.a -> nowhere"):
        tree.validate()


def test_signature_ignores_names_positions_and_colours() -> None:
    renamed = TREE.replace('"height"', '"h"').replace("{ 100, 0, }", "{ 5, 5, }").replace("1, 1, 1, 1", "0, 0, 0, 1")
    changed = TREE.replace("{ 1, 1, }, }", "{ 1, 2, }, }")

    signature = TreeSignature(DataDocument.parse(TREE)).signature()

    assert TreeSignature(DataDocument.parse(renamed)).signature() == signature
    assert TreeSignature(DataDocument.parse(changed)).signature() != signature
