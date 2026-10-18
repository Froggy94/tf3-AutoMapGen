"""Lua data values: ordered tables (array and keyed entries), numbers kept as written, calls like _("x")."""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

type LuaKey = str | int | None          # None = array entry
type LuaValue = LuaTable | LuaNumber | LuaExpression | LuaCall | str | bool | None


@dataclass(frozen=True)
class LuaNumber:
    """A number with its Lua source text; parsed numbers are written back unchanged."""

    text: str

    @classmethod
    def of(cls, value: float) -> LuaNumber:
        """Number from Python; formatted like the legacy builder (`:g`, 6 significant digits)."""
        return cls(f"{value:g}")

    @property
    def value(self) -> float:
        return float(self.text)


@dataclass(frozen=True)
class LuaExpression:
    """Arithmetic of number literals as written in the source, e.g. `1 / 32` in material files."""

    text: str

    @property
    def value(self) -> float:
        return _evaluate(ast.parse(self.text, mode="eval").body)


def _evaluate(node: ast.expr) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_evaluate(node.operand)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_evaluate(node.left), _evaluate(node.right))
    raise ValueError(f"unsupported expression: {ast.dump(node)}")


_OPERATORS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
}


@dataclass(frozen=True)
class LuaCall:
    """Function call in data, e.g. _("Temperate") for translated texts."""

    function: str
    args: tuple[LuaValue, ...]


class LuaTable:
    """Ordered Lua table; keyed entries are unique, array entries keep their position."""

    def __init__(self, entries: list[tuple[LuaKey, LuaValue]] | None = None) -> None:
        self._entries: list[tuple[LuaKey, LuaValue]] = list(entries or [])

    def __repr__(self) -> str:
        return f"LuaTable({self._entries!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, LuaTable) and self._entries == other._entries

    def __hash__(self) -> int:
        raise TypeError("LuaTable is mutable and not hashable")

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return any(k == key for k, _ in self._entries if k is not None)

    def __getitem__(self, key: str | int) -> LuaValue:
        for k, v in self._entries:
            if k == key:
                return v
        raise KeyError(key)

    def __setitem__(self, key: str | int, value: LuaValue) -> None:
        for i, (k, _) in enumerate(self._entries):
            if k == key:
                self._entries[i] = (key, value)
                return
        self._entries.append((key, value))

    def get(self, key: str | int, default: LuaValue = None) -> LuaValue:
        for k, v in self._entries:
            if k == key:
                return v
        return default

    def table(self, key: str | int) -> LuaTable:
        """Value at `key` that must be a table."""
        value = self[key]
        if not isinstance(value, LuaTable):
            raise TypeError(f"{key!r} is not a table")
        return value

    def remove(self, key: str | int) -> None:
        self._entries = [(k, v) for k, v in self._entries if k != key]

    def append(self, value: LuaValue) -> None:
        self._entries.append((None, value))

    def insert_array(self, index: int, value: LuaValue) -> None:
        """Insert an array entry before the array entry at `index` (0-based among array entries)."""
        positions = [i for i, (k, _) in enumerate(self._entries) if k is None]
        at = positions[index] if index < len(positions) else len(self._entries)
        self._entries.insert(at, (None, value))

    def entries(self) -> Iterator[tuple[LuaKey, LuaValue]]:
        return iter(self._entries)

    def array(self) -> list[LuaValue]:
        return [v for k, v in self._entries if k is None]

    def keyed(self) -> list[tuple[str | int, LuaValue]]:
        return [(k, v) for k, v in self._entries if k is not None]


def to_plain(value: LuaValue) -> Any:
    """Plain Python structure for comparisons: numbers as float, keyed entries as sorted dict, calls as dict."""
    if isinstance(value, LuaTable):
        array = [to_plain(v) for v in value.array()]
        keyed = {str(k): to_plain(v) for k, v in value.keyed()}
        if not keyed:
            return array
        if array:
            keyed["[array]"] = array
        return dict(sorted(keyed.items()))
    if isinstance(value, LuaNumber | LuaExpression):
        return value.value
    if isinstance(value, LuaCall):
        return {"[call]": value.function, "args": [to_plain(a) for a in value.args]}
    return value
