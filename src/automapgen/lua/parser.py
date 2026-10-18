"""Parser for the Lua data subset used by the game's data files.

Supported: tables with array entries, `name = v`, `["key"] = v`, `[number] = v`; strings ("...", '...', [[...]]),
numbers, true/false/nil, calls `name(args)`, line and block comments.
"""

from __future__ import annotations

import re

from automapgen.lua.values import LuaCall, LuaExpression, LuaKey, LuaNumber, LuaTable, LuaValue

_NUMBER = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_OPERATOR = re.compile(r"\s*[-+*/]\s*")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\"}


class LuaSyntaxError(ValueError):
    """The text is not valid Lua data."""


class LuaParser:
    """Recursive-descent parser over one source text."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._pos = 0

    def parse_value(self) -> LuaValue:
        self._skip()
        char = self._peek()
        if char == "{":
            return self._table()
        if char in "\"'":
            return self._quoted_string()
        if self._text.startswith("[[", self._pos) or self._text.startswith("[=", self._pos):
            return self._long_string()
        number = _NUMBER.match(self._text, self._pos)
        if number:
            self._pos = number.end()
            return self._arithmetic(number.group())
        name = self._name()
        if name == "true":
            return True
        if name == "false":
            return False
        if name == "nil":
            return None
        return self._call(name)

    def expect_keyword(self, word: str) -> None:
        self._skip()
        if self._name() != word:
            raise self._error(f"expected '{word}'")

    def expect(self, token: str) -> None:
        self._skip()
        if not self._text.startswith(token, self._pos):
            raise self._error(f"expected '{token}'")
        self._pos += len(token)

    def expect_end_of_text(self) -> None:
        self._skip()
        if self._pos != len(self._text):
            raise self._error("unexpected text after the end")

    def _table(self) -> LuaTable:
        self.expect("{")
        table = LuaTable()
        while True:
            self._skip()
            if self._peek() == "}":
                self._pos += 1
                return table
            key, value = self._entry()
            if key is None:
                table.append(value)
            else:
                table[key] = value
            self._skip()
            if self._peek() and self._peek() in ",;":
                self._pos += 1
            elif self._peek() != "}":
                raise self._error("expected ',' or '}'")

    def _entry(self) -> tuple[LuaKey, LuaValue]:
        if self._peek() == "[" and not self._text.startswith("[[", self._pos):
            self._pos += 1
            key_value = self.parse_value()
            self.expect("]")
            self.expect("=")
            if isinstance(key_value, LuaNumber):
                return int(key_value.value), self.parse_value()
            if isinstance(key_value, str):
                return key_value, self.parse_value()
            raise self._error("table key must be a string or number")
        name = _NAME.match(self._text, self._pos)
        if name and name.group() not in ("true", "false", "nil"):
            after = name.end()
            while after < len(self._text) and self._text[after] in " \t\r\n":
                after += 1
            if self._text.startswith("=", after) and not self._text.startswith("==", after):
                self._pos = after + 1
                return name.group(), self.parse_value()
        return None, self.parse_value()

    def _arithmetic(self, first: str) -> LuaNumber | LuaExpression:
        """A number, or number literals joined by + - * / (kept as written)."""
        end = self._pos
        while True:
            op = _OPERATOR.match(self._text, end)
            operand = _NUMBER.match(self._text, op.end()) if op else None
            if not (op and operand):
                break
            end = operand.end()
        if end == self._pos:
            return LuaNumber(first)
        text = first + self._text[self._pos:end]
        self._pos = end
        return LuaExpression(text)

    def _call(self, name: str) -> LuaCall:
        self.expect("(")
        args: list[LuaValue] = []
        self._skip()
        while self._peek() != ")":
            args.append(self.parse_value())
            self._skip()
            if self._peek() == ",":
                self._pos += 1
        self._pos += 1
        return LuaCall(name, tuple(args))

    def _quoted_string(self) -> str:
        quote = self._text[self._pos]
        self._pos += 1
        parts: list[str] = []
        while True:
            if self._pos >= len(self._text):
                raise self._error("unterminated string")
            char = self._text[self._pos]
            if char == quote:
                self._pos += 1
                return "".join(parts)
            if char == "\\":
                escaped = self._text[self._pos + 1]
                parts.append(_ESCAPES.get(escaped, escaped))
                self._pos += 2
            else:
                parts.append(char)
                self._pos += 1

    def _long_string(self) -> str:
        match = re.compile(r"\[(=*)\[").match(self._text, self._pos)
        if not match:
            raise self._error("invalid long string")
        close = f"]{match.group(1)}]"
        end = self._text.find(close, match.end())
        if end < 0:
            raise self._error("unterminated long string")
        self._pos = end + len(close)
        content = self._text[match.end():end]
        return content[1:] if content.startswith("\n") else content

    def _name(self) -> str:
        match = _NAME.match(self._text, self._pos)
        if not match:
            raise self._error("unexpected character")
        self._pos = match.end()
        return match.group()

    def _peek(self) -> str:
        return self._text[self._pos] if self._pos < len(self._text) else ""

    def _skip(self) -> None:
        while self._pos < len(self._text):
            if self._text[self._pos] in " \t\r\n":
                self._pos += 1
            elif self._text.startswith("--[[", self._pos):
                end = self._text.find("]]", self._pos)
                self._pos = len(self._text) if end < 0 else end + 2
            elif self._text.startswith("--", self._pos):
                end = self._text.find("\n", self._pos)
                self._pos = len(self._text) if end < 0 else end + 1
            else:
                return

    def _error(self, message: str) -> LuaSyntaxError:
        line = self._text.count("\n", 0, self._pos) + 1
        return LuaSyntaxError(f"line {line}: {message}")
