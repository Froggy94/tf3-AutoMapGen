"""Writes documents as TOML (values first, then sub-tables with [dotted.headers]) - for files the application
maintains itself (projects written by the GUI, local settings)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+\Z")
INLINE_MAX_KEYS = 4
INLINE_MAX_WIDTH = 90


class TomlWriter:
    def write(self, document: Mapping[str, Any], header: str = "") -> str:
        lines = [f"# {line}".rstrip() for line in header.splitlines()]
        self._table(document, [], lines)
        return "\n".join(lines).strip("\n") + "\n"

    def _table(self, table: Mapping[str, Any], path: list[str], lines: list[str]) -> None:
        values = {k: v for k, v in table.items() if not isinstance(v, Mapping) or self._inline(v)}
        tables = {k: v for k, v in table.items() if isinstance(v, Mapping) and not self._inline(v)}
        if path and (values or not tables):
            lines.append("")
            lines.append("[" + ".".join(self._key(p) for p in path) + "]")
        for key, value in values.items():
            lines.append(f"{self._key(key)} = {self._value(value)}")
        for key, value in tables.items():
            self._table(value, [*path, key], lines)

    def _inline(self, table: Mapping[str, Any]) -> bool:
        """Small tables of plain values are written on one line: { west = 11.4, south = 47.2 }."""
        if not table or len(table) > INLINE_MAX_KEYS or any(isinstance(v, Mapping | list) for v in table.values()):
            return False
        return len(self._value(table)) <= INLINE_MAX_WIDTH

    @staticmethod
    def _key(key: str) -> str:
        return key if _BARE_KEY.match(key) else json.dumps(key, ensure_ascii=False)

    def _value(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int | float):
            return repr(value)
        if isinstance(value, str):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, list | tuple):
            return "[" + ", ".join(self._value(v) for v in value) + "]"
        if isinstance(value, Mapping):
            return "{ " + ", ".join(f"{self._key(k)} = {self._value(v)}" for k, v in value.items()) + " }"
        raise TypeError(f"cannot write {value!r} as TOML value")
