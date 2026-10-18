"""Mod that builds the field tracks and hiking paths (node paths.plan) as real streets of own street types.

Own street types without paving: only the ground texture under the street is visible (fillGroundTex), like the game's
old country road. Streets clear trees themselves and adapt the terrain. Junctions are not built (crash without
BaseNodeConfig); where a path comes closer to another path or a planned road than the gap, a gap stays. A piece the game
rejects is halved, the rest stays a gap. Log: local/crash_dump/stdout.txt, lines "[dhe_path_streets]".
"""

from __future__ import annotations

import hashlib
from typing import NamedTuple

import numpy as np
from pydantic import BaseModel, ConfigDict

from automapgen.graph import NodeContext, NodeParams, node
from automapgen.nodes.climate.preset import Optional
from automapgen.nodes.lines import PathPlan
from automapgen.nodes.map_mods.path_network import (
    BuildArea,
    CurveParams,
    MapBox,
    PathClipper,
    PathNetworkParams,
    PathNetworkPlanner,
    ScriptNetwork,
)
from automapgen.nodes.map_mods.roads import game_size
from automapgen.nodes.map_mods.template import LuaTemplate, lua_bool, lua_list, lua_number
from automapgen.nodes.maps import MapFormat
from automapgen.nodes.mods import CONTENT, ModFiles, Translations
from automapgen.nodes.roads.plan import RoadPlan

SCRIPT_DIR = "dhe_path_streets"
STREET_DIR = "infrastructure/street/dhe"
_LANE_OVERLAY = '''		streetLane = {
			name = "/infrastructure/street/country/mat/old_country_lane.mtl",
			size = { 4.0, 4.0 }
		},
		crossingLane = {
			name = "/infrastructure/street/country/mat/old_country_lane.mtl",
			size = { 4.0, 4.0 }
		},
'''


class _Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class Lane(_Value):
    forward: bool
    """Driving direction."""
    offset: float
    """Offset from the centre line."""
    width: float
    """Lane width (metres)."""
    modes: tuple[str, ...]
    """Transport modes."""


class StreetType(_Value):
    file: str
    """File name of the street type."""
    name: str
    """Name in the game (English)."""
    name_de: str
    """Name in the game (German)."""
    fill: str
    """Ground texture under the street (material)."""
    lane_overlay: bool
    """Driving lanes of the old country road on top."""
    lanes: tuple[Lane, ...]
    """Lanes (walkway, lane, lane, walkway)."""
    street_modes: tuple[str, ...]
    """Transport modes of the street."""
    speed: float
    """Speed (m/s)."""


class PathStreetModSettings(_Value):
    """Settings of the path street mod (a value of configs/map_mods.toml)."""

    test_area: Optional[BuildArea] = None
    """Absent: the whole map; else only a test section."""
    network: PathNetworkParams
    """Junctions, long sections, gaps."""
    curves: CurveParams
    """Curves and nodes."""
    clear_game_m: float
    """In game: leave a node out if a street lies this close already (town streets are not in the plan; path ends
    near other streets crash the game)."""
    street_types: dict[str, StreetType]
    """Street type per path kind of the path plan."""
    max_slope: float
    """Steepest slope of the street types (1 = 45°; the game's country roads much less)."""
    flip_y: bool
    """If the paths lie mirrored at the north-south axis in game."""
    description: str
    """Description of the street types in the game (English, {name} = name)."""
    description_de: str
    """Description of the street types in the game (German, {name} = German name)."""

    def strings_de(self) -> dict[str, str]:
        """German translations of the street types' names and descriptions."""
        out = {}
        for t in self.street_types.values():
            out[t.name] = t.name_de
            out[self.description.format(name=t.name)] = self.description_de.format(name=t.name_de)
        return out


class PathStreetFiles:
    """The files of the street types and the game script with the path network."""

    def __init__(self, settings: PathStreetModSettings) -> None:
        self._settings = settings

    def files(self, network: ScriptNetwork, aspect: float, mod_id: str) -> dict[str, str]:
        """Paths below the mod folder -> text."""
        files = {}
        for t in self._settings.street_types.values():
            street = f"{CONTENT}{STREET_DIR}/{t.file}"
            files[f"{street}.street_template.lua"] = self.street_template(t)
            files[f"{street}.street.lua"] = self.street_style(t)
            files[f"{street}_fill.gtex.lua"] = LuaTemplate.named("path_street_fill.gtex.lua").render({"FILL": t.fill})
        script = f"{CONTENT}{SCRIPT_DIR}/{SCRIPT_DIR}"
        files[f"{script}.script.lua"] = self.script(network, aspect, mod_id)
        files[f"{script}.gs.lua"] = LuaTemplate.named("path_streets.gs.lua").text
        return files

    def script(self, network: ScriptNetwork, aspect: float, mod_id: str) -> str:
        s = self._settings
        template = LuaTemplate.named("path_streets.script.lua")
        templates = ", ".join(f'"{t.file}"' for t in s.street_types.values())
        version = hashlib.md5((template.text + network.nodes + network.chains + network.kinds + network.junctions
                               + templates).encode()).hexdigest()[:12]
        return template.render({
            "VERSION": version, "FLIPY": lua_bool(s.flip_y), "ASPECT": f"{aspect:.6f}", "MODID": mod_id,
            "CLEAR": lua_number(s.clear_game_m), "TEMPLATES": templates, "NODES": network.nodes,
            "CHAINS": network.chains, "KINDS": network.kinds, "JUNCTIONS": network.junctions})

    def street_template(self, t: StreetType) -> str:
        lanes = "".join(
            f"\t\t\t{{\n\t\t\t\tforward = {lua_bool(lane.forward)},\n\t\t\t\theight = 0,\n"
            f"\t\t\t\toffset = {lua_number(lane.offset)},\n\t\t\t\tspeed = {lua_number(t.speed)},\n"
            f"\t\t\t\ttransportModes = {lua_list(lane.modes)},\n\t\t\t\twidth = {lua_number(lane.width)},\n\t\t\t}},\n"
            for lane in t.lanes)
        return LuaTemplate.named("path_street.street_template.lua").render({
            "NAME": t.name, "DESCRIPTION": self._settings.description.format(name=t.name), "FILE": t.file,
            "LANES": lanes, "MAX_SLOPE": str(float(self._settings.max_slope)),
            "STREET_MODES": lua_list(t.street_modes)})

    @staticmethod
    def street_style(t: StreetType) -> str:
        return LuaTemplate.named("path_street.street.lua").render(
            {"LANE_OVERLAY": _LANE_OVERLAY if t.lane_overlay else ""})


class PathStreetModParams(NodeParams):
    path_street_mod: PathStreetModSettings
    """Settings of the path street mod."""


class PathStreetMod(NamedTuple):
    files: ModFiles
    translations: Translations


@node("mod.path_streets")
def path_street_mod(paths: PathPlan, map_format: MapFormat, mod_id: str, params: PathStreetModParams,
                    context: NodeContext, roads: RoadPlan | None = None) -> PathStreetMod:
    """The content of the path street mod: own street types and a game script that builds the planned field tracks
    and hiking paths as streets, with gaps near the planned roads (if any); the German names of the street types."""
    s = params.path_street_mod
    game_w, game_h = game_size(map_format)
    scale = np.array([game_w, game_h])
    kinds = list(s.street_types)
    clipper = PathClipper(scale, MapBox.of(scale, s.test_area))
    road_points = clipper.road_points(roads.lines() if roads else [], s.network.gap_game_m)
    network = PathNetworkPlanner(s.network, s.curves).plan(clipper.lines(paths.data, kinds), kinds, road_points)
    context.log(f"junctions: {network.junction_count}, connected path ends: {network.connected_ends}; "
                f"{len(network.sections)} sections, {network.edge_count} edges, gaps at {network.gaps} places "
                f"(+{network.sharp_gaps} at sharp bends); " + ", ".join(
                    f"{t.name} {network.length_game_m(k) / 1000:.1f} km" for k, t in s.street_types.items()))
    files = PathStreetFiles(s).files(ScriptNetwork.encode(network, kinds, scale), game_w / game_h, mod_id)
    return PathStreetMod(ModFiles.of({path: text.encode("utf-8") for path, text in files.items()}),
                         Translations(s.strings_de()))
