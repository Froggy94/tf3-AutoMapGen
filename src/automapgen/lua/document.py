"""A game data file: `function data() return <table> end`."""

from __future__ import annotations

from pathlib import Path

from automapgen.lua.parser import LuaParser, LuaSyntaxError
from automapgen.lua.values import LuaTable
from automapgen.lua.writer import LuaWriter


class DataDocument:
    """Root table of a Lua data file; parse, modify, write."""

    def __init__(self, root: LuaTable) -> None:
        self.root = root

    @classmethod
    def parse(cls, text: str) -> DataDocument:
        parser = LuaParser(text.lstrip("﻿"))
        parser.expect_keyword("function")
        parser.expect_keyword("data")
        parser.expect("(")
        parser.expect(")")
        parser.expect_keyword("return")
        root = parser.parse_value()
        if not isinstance(root, LuaTable):
            raise LuaSyntaxError("data() must return a table")
        parser.expect_keyword("end")
        parser.expect_end_of_text()
        return cls(root)

    @classmethod
    def read(cls, path: Path) -> DataDocument:
        try:
            return cls.parse(path.read_text(encoding="utf-8"))
        except LuaSyntaxError as error:
            raise LuaSyntaxError(f"{path}: {error}") from error

    def to_lua(self) -> str:
        """Layout of the game editor: entries of the root table at depth 2, closing brace at depth 1."""
        return f"function data()\nreturn {LuaWriter().value(self.root, 2)}\nend\n"

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_lua(), encoding="utf-8", newline="\n")
