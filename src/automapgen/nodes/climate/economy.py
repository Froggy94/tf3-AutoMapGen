"""Own economy of the climate (optional): a modified copy of the base climate's economy (today only temperate).

Economy files are Lua code (functions, loops), not data, so they cannot use the Lua object model. This module is the
documented exception to "no text patches": every replacement must match exactly once, so game updates fail loudly.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import NamedTuple

from automapgen.graph import NodeContext, NodeParams, node
from automapgen.nodes.climate.definition import ClimaDefinitionEditor
from automapgen.nodes.climate.package import ClimateIdentityData
from automapgen.nodes.climate.preset import EconomySettings
from automapgen.nodes.gamefiles import GameFiles, LuaData
from automapgen.nodes.industries import IndustrySettings
from automapgen.nodes.mods import CONTENT, ModFiles

_CARGO_TABLE = "local cargoRequiredIndustries = {"


def _replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"economy: text not found exactly once (game update?): {old!r}")
    return text.replace(old, new)


class EconomyPatcher:
    """temperate.eco without oil platforms (refinery at oil wells, glass works at sand pits), optionally with the
    industries and cargos of other climates unlocked."""

    BASE_ECONOMY = "temperate"       # the patches below are written for this game economy

    def __init__(self, settings: EconomySettings, extra_industries: Sequence[str], extra_cargos: Sequence[str],
                 log: Callable[[str], None]) -> None:
        self._p = settings
        self._extra_industries = extra_industries
        self._extra_cargos = extra_cargos
        self._log = log

    def patched(self, sources: Mapping[str, str]) -> str:
        """`sources`: the game's economies temperate, subarctic and tropic (Lua source)."""
        p = self._p
        text = self._with_cargo_table(sources)
        if p.extra_industries:
            for name in self._extra_industries:
                text = text.replace(f"\tplacementParams.{name} = nil\n", "")
            for cargo in self._extra_cargos:
                text = text.replace(f"\tcargoCategories.{cargo} = nil\n", "")
            text = _replace_once(text, '\tplacementparamsutil.removeTags(placementParams, {"OUTPUT_RUBBER"})\n', "")
        text = _replace_once(text, 'placementParams.glass_works.requiredIndustryTags = {"sand_excavator"}',
                             'placementParams.glass_works.requiredIndustryTags = {"sand_pit"}')
        text = _replace_once(text, 'placementParams.oil_refinery.requiredIndustryTags = {"oil_platform"}',
                             'placementParams.oil_refinery.requiredIndustryTags = {"oil_well"}\n'
                             + "".join(f"\tplacementParams.{n} = nil" for n in p.removed_industries))
        text = _replace_once(text, "\tlocal cargoCategories = placementparamsutil.getCargoCategories()\n",
                             "\tlocal cargoCategories = placementparamsutil.getCargoCategories()\n"
                             '\tcargoCategories.crude_oil.requiredIndustryTags = {"oil_well"}\n')
        text = _replace_once(text, '\t\t\toil_platform = "oil_well",\n', "")
        text = _replace_once(text, "\t\t\toil_platform = 0.01,\n", "\t\t\toil_well = 0.01,\n")
        text = _replace_once(text, 'name = _("Temperate")', f'name = _("{self.name}")')
        text = _replace_once(text, 'icon = "temperate.tga"', 'icon = "::/economy/temperate.tga"')
        text = _replace_once(text, "waterIndustryFraction = 6.0 / 41.0,",
                             f"waterIndustryFraction = {p.water_industry_fraction},")
        rest = text
        for name in p.removed_industries:
            rest = rest.replace(f"\tplacementParams.{name} = nil\n", "")
        if any(name in rest for name in p.removed_industries):
            raise ValueError(f"economy still contains {list(p.removed_industries)}")
        return text

    @property
    def name(self) -> str:
        return self._p.name_with_extra_industries if self._p.extra_industries else self._p.name

    def _with_cargo_table(self, sources: Mapping[str, str]) -> str:
        """cargoRequiredIndustries of temperate (plus other climates if unlocked), oil platform -> oil well."""
        blocks = self._cargo_blocks(sources["temperate"])
        if self._p.extra_industries:
            for other in ("subarctic", "tropic"):
                for cargo, lines in self._cargo_blocks(sources[other]).items():
                    blocks.setdefault(cargo, lines)
            missing = [c for c in self._extra_cargos if c not in blocks]
            if missing:   # intermediate products have no list in the original either
                self._log(f"economy: without cargoRequiredIndustries (as in the original): {', '.join(missing)}")
        table = _CARGO_TABLE + "\n" + "".join(
            f"\t\t{cargo} = {{\n{self._fix_lines(lines)}\n\t\t}},\n" for cargo, lines in sorted(blocks.items())) + "\t}"
        text = sources["temperate"]
        start = text.index(_CARGO_TABLE)
        end = text.index("\n\t}\n", start) + 3
        return text[:start] + table + text[end:]

    @staticmethod
    def _cargo_blocks(text: str) -> dict[str, str]:
        body = text[text.index(_CARGO_TABLE):]
        body = body[:body.index("\n\t}\n")]
        return dict(re.findall(r"\n\t\t(\w+) = \{\n(.*?)\n\t\t\},", body, re.S))

    def _fix_lines(self, lines: str) -> str:
        """Oil platform -> oil well, drop duplicate entries."""
        out, seen = [], set()
        for line in lines.split("\n"):
            match = re.match(r"\t+(\w+) = ", line)
            key = match.group(1) if match else line
            if key in self._p.removed_industries:
                line, key = line.replace(key, "oil_well", 1), "oil_well"
            if key not in seen:
                seen.add(key)
                out.append(line)
        return "\n".join(out)


class EconomyParams(NodeParams):
    economy: EconomySettings = EconomySettings()
    """The climate's economy."""
    industries: IndustrySettings
    """The industry planning (industries and cargos of other climates in the industry mod)."""


class Economy(NamedTuple):
    files: ModFiles
    path: str


@node("climate.economy")
def economy(clima: LuaData, identity: ClimateIdentityData, params: EconomyParams, context: NodeContext) -> Economy:
    """The own economy (economy/<id>.eco.lua) and its path for the climate definition; none (empty path) when the
    climate keeps the game's economy."""
    base = ClimaDefinitionEditor.base_economy(clima.document())
    if not params.economy.custom:
        context.log(f"economy: {base} (game, unchanged)")
        return Economy(ModFiles(()), "")
    if base != EconomyPatcher.BASE_ECONOMY:
        raise ValueError(f"an own economy only exists for the {EconomyPatcher.BASE_ECONOMY} economy, not for "
                         f"{base!r} - set economy.custom = false")
    files = GameFiles.at(context.game_folder())
    sources = {name: files.text(f"economy/{name}.eco.lua") for name in ("temperate", "subarctic", "tropic")}
    patcher = EconomyPatcher(params.economy, params.industries.extra_industries, params.industries.extra_cargos,
                             context.log)
    i = identity.identity()
    context.log(f"economy {patcher.name}: removed {', '.join(params.economy.removed_industries)}")
    return Economy(ModFiles.of({f"{CONTENT}economy/{i.file_id}.eco.lua": patcher.patched(sources).encode("utf-8")}),
                   f"{i.mod_id}::/economy/{i.file_id}.eco")
