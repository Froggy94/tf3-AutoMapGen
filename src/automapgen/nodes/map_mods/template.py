"""Game scripts and game files from templates with named placeholders (@NAME@), instead of text patching in code.

The templates are part of the code (package resources next to this module), not data of a run.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from importlib import resources

_PLACEHOLDER = re.compile(r"@([A-Z][A-Z0-9_]*)@")


class LuaTemplate:
    """A template; every placeholder must get a value and every value a placeholder."""

    def __init__(self, text: str, name: str = "template") -> None:
        self._text = text
        self._name = name

    @classmethod
    def named(cls, file_name: str) -> LuaTemplate:
        """A template of the folder `templates` of this package."""
        folder = resources.files("automapgen.nodes.map_mods").joinpath("templates")
        return cls(folder.joinpath(file_name).read_text(encoding="utf-8"), file_name)

    @property
    def text(self) -> str:
        return self._text

    def render(self, values: Mapping[str, str]) -> str:
        names = set(_PLACEHOLDER.findall(self._text))
        missing, unknown = sorted(names - values.keys()), sorted(values.keys() - names)
        if missing or unknown:
            raise ValueError(f"{self._name}: placeholders without value {missing}, values without placeholder "
                             f"{unknown}")
        return _PLACEHOLDER.sub(lambda m: values[m.group(1)], self._text)


def lua_bool(value: bool) -> str:
    return "true" if value else "false"


def lua_number(value: float) -> str:
    """Whole numbers without decimals (as in the hand-written game files)."""
    return str(int(value)) if float(value).is_integer() else str(value)


def lua_list(items: Sequence[str]) -> str:
    return "{ " + "".join(f'"{s}", ' for s in items) + "}"
