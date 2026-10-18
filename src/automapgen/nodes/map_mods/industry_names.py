"""Mod with a game script that names every industry after the nearest town of the map ("<town> <type>").

The towns/industries import knows no names, so the game takes a random town name from its list. The script runs after
loading and then regularly (industries founded later): per industry the nearest planned town, type = game name without
its name stem (game language), else the type name of the industry planning. Industries renamed by the player are
kept. Log: local/crash_dump/stdout.txt, lines "[dhe_industry_names]".
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from automapgen.graph import NodeContext, NodeParams, node
from automapgen.nodes.industries import IndustrySettings
from automapgen.nodes.map_mods.template import LuaTemplate
from automapgen.nodes.mods import CONTENT, ModFiles
from automapgen.nodes.towns import TownList, lua_string

SCRIPT_DIR = "dhe_industry_names"


class IndustryNamesModSettings(BaseModel):
    """Settings of the industry names mod (a value of configs/map_mods.toml)."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    wait_ticks: int
    """Wait this many simulation steps after loading."""
    recheck_ticks: int
    """Then check for new industries this often (simulation steps)."""
    log_examples: int
    """Log this many renamings one by one (to check the name stem)."""


class IndustryNamesScript:
    """The game script with the towns and the type names."""

    def __init__(self, settings: IndustryNamesModSettings, type_names: Mapping[str, str]) -> None:
        self._settings = settings
        self._type_names = type_names

    def render(self, towns: Sequence[Mapping[str, Any]]) -> str:
        """`towns`: [{name, x, y}], game metres from the map centre."""
        towns_lua = "\n".join(f"\t{{ {float(t['x']):.1f}, {float(t['y']):.1f}, {lua_string(str(t['name']))} }},"
                              for t in towns)
        types_lua = "\n".join(f"\t{key} = {lua_string(name)}," for key, name in self._type_names.items())
        s = self._settings
        return LuaTemplate.named("industry_names.script.lua").render({
            "VERSION": hashlib.md5(towns_lua.encode()).hexdigest()[:12], "WAIT": str(s.wait_ticks),
            "RECHECK": str(s.recheck_ticks), "EXAMPLES": str(s.log_examples), "TOWNS": towns_lua,
            "TYPES": types_lua})


class IndustryNamesModParams(NodeParams):
    industry_names_mod: IndustryNamesModSettings
    """Settings of the industry names mod."""
    industries: IndustrySettings
    """The industry planning (its type names)."""


@node("mod.industry_names", output="files")
def industry_names_mod(towns: TownList, params: IndustryNamesModParams, context: NodeContext) -> ModFiles:
    """The content of the industry names mod: a game script that names the industries after the nearest town."""
    script = IndustryNamesScript(params.industry_names_mod, params.industries.naming.type_names).render(towns.data)
    context.log(f"{len(towns.data)} towns")
    folder = f"{CONTENT}{SCRIPT_DIR}/{SCRIPT_DIR}"
    return ModFiles.of({f"{folder}.script.lua": script.encode("utf-8"),
                        f"{folder}.gs.lua": LuaTemplate.named("industry_names.gs.lua").text.encode("utf-8")})
