"""Test mod for the road planning: a game script that builds test roads next to every street the player builds,
to find out what the game does with given heights (its own cut and fill), bridge and tunnel edges, a junction to an
existing node, hairpins of different radii and the steep streets with retaining walls of the mod
DHE_Walls_Tracks_Streets (edge decorations). Not bound to a map; results in local/crash_dump/stdout.txt, lines
"[dhe_road_test]".
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from automapgen.graph import NodeParams, node
from automapgen.nodes.map_mods.template import LuaTemplate
from automapgen.nodes.mods import CONTENT, ModFiles

SCRIPT_DIR = "dhe_road_test"


class RoadTestSettings(BaseModel):
    """Settings of the road test mod (a value of configs/road_test.toml)."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    template: str
    """Street template of the test roads."""
    walls_mod_id: str
    """Mod ID of DHE_Walls_Tracks_Streets (its resources are also looked up as <mod ID>::/...)."""
    steep_template: str
    """Steep street template of DHE_Walls_Tracks_Streets (T5)."""
    lower_walls: tuple[str, ...]
    """Names tried for the retaining wall below the road (fill; edge decoration)."""
    upper_walls: tuple[str, ...]
    """Names tried for the retaining wall above the road (cut)."""
    wall_from_m: float
    """T5: walls where the line lies more than this above (fill) or below (cut) the ground."""
    bridge_types: tuple[str, ...]
    """Names tried for the bridge type (the first one the game knows is used; all are logged)."""
    tunnel_types: tuple[str, ...]
    """Names tried for the tunnel type."""
    bridge_above_m: float
    """Profile line: bridge where the line lies more than this above the ground."""
    tunnel_below_m: float
    """Profile line: tunnel where the line lies more than this below the ground."""
    line_length_m: float
    """Length of the profile lines (straight, constant grade between the ground at both ends)."""
    edge_length_m: float
    """Length of the edges."""
    line_offset_m: float
    """Distance between the test roads."""
    branch_length_m: float
    """Length of the branch from the middle node of the second profile line (junction)."""
    radii_m: tuple[float, ...]
    """Hairpins with these radii."""
    leg_length_m: float
    """Length of the straight legs of the hairpins."""


class RoadTestParams(NodeParams):
    road_test: RoadTestSettings
    """Settings of the road test mod."""


def _names(names: tuple[str, ...]) -> str:
    return ", ".join(f'"{n}"' for n in names)


@node("mod.road_test", output="files")
def road_test_mod(params: RoadTestParams) -> ModFiles:
    """The content of the road test mod: the game script and its registration."""
    s = params.road_test
    script = LuaTemplate.named("road_test.script.lua").render({
        "TEMPLATE": s.template, "WALLS_MOD_ID": s.walls_mod_id, "STEEP_TEMPLATE": s.steep_template,
        "LOWER_WALLS": _names(s.lower_walls), "UPPER_WALLS": _names(s.upper_walls),
        "WALL_FROM": f"{s.wall_from_m:g}", "BRIDGE_TYPES": _names(s.bridge_types),
        "TUNNEL_TYPES": _names(s.tunnel_types), "BRIDGE_ABOVE": f"{s.bridge_above_m:g}",
        "TUNNEL_BELOW": f"{s.tunnel_below_m:g}", "LINE_LENGTH": f"{s.line_length_m:g}",
        "EDGE_LENGTH": f"{s.edge_length_m:g}", "LINE_OFFSET": f"{s.line_offset_m:g}",
        "BRANCH_LENGTH": f"{s.branch_length_m:g}", "RADII": ", ".join(f"{r:g}" for r in s.radii_m),
        "LEG_LENGTH": f"{s.leg_length_m:g}"})
    folder = f"{CONTENT}{SCRIPT_DIR}/{SCRIPT_DIR}"
    return ModFiles.of({f"{folder}.script.lua": script.encode("utf-8"),
                        f"{folder}.gs.lua": LuaTemplate.named("road_test.gs.lua").text.encode("utf-8")})
