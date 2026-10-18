"""Writes Lua data in the layout of the game's node editor (tabs, trailing commas, short arrays on one line)."""

from __future__ import annotations

import re

from automapgen.lua.values import LuaCall, LuaExpression, LuaKey, LuaNumber, LuaTable, LuaValue

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_RESERVED = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local", "nil",
             "not", "or", "repeat", "return", "then", "true", "until", "while"}


class LuaWriter:
    """Serialises Lua values; `depth` is the tab depth of the entries of the table being written."""

    def value(self, value: LuaValue, depth: int) -> str:
        if isinstance(value, LuaTable):
            return self._table(value, depth)
        if isinstance(value, LuaNumber | LuaExpression):
            return value.text
        if isinstance(value, LuaCall):
            return f"{value.function}({', '.join(self.value(a, depth) for a in value.args)})"
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return "nil"
        return self._string(value)

    def _table(self, table: LuaTable, depth: int) -> str:
        if not len(table):
            return "{ }"
        if self._is_inline(table):
            return "{ " + "".join(f"{self.value(v, depth)}, " for v in table.array()) + "}"
        inner = "\t" * depth
        lines = [f"{inner}{self._key(k)}{self.value(v, depth + 1)},\n" for k, v in table.entries()]
        opening = "{ \n" if table.keyed() else "{\n"   # editor layout: lists without, keyed tables with a space
        return opening + "".join(lines) + "\t" * (depth - 1) + "}"

    @staticmethod
    def _is_inline(table: LuaTable) -> bool:
        return not table.keyed() and all(not isinstance(v, LuaTable) for v in table.array())

    def _key(self, key: LuaKey) -> str:
        if key is None:
            return ""
        if isinstance(key, int):
            return f"[{key}] = "
        if _IDENTIFIER.match(key) and key not in _RESERVED:
            return f"{key} = "
        return f"[{self._string(key)}] = "

    @staticmethod
    def _string(text: str) -> str:
        if "\n" in text and "]]" not in text:
            return f"[[{text}]]"
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
