"""Mod with a game script that builds the planned road network (node roads.network).

The build starts shortly after the game starts (auto_start), on a start signal (the player builds a short piece of
a street whose type contains start_marker) or from the game's console (event "start" of "dhe_roads"). The script
builds way by way with the planned heights, bridges and tunnels as their edge type, and attaches a way to the nodes
already built where ways meet (junctions); it logs the progress (local/crash_dump/stdout.txt, lines "[dhe_roads]").
A constant offset between the planned heights and the game terrain (minimum height entered at import) is measured
on the normal ways and corrected. A way the game rejects is split and its parts are built one by one, what still
fails stays a gap. Building after TPF3 Map Studio's import mod (MIT, github.com/sharpi13822/TPF3-Map-Studio).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from pydantic import BaseModel, ConfigDict

from automapgen.graph import NodeContext, NodeParams, node
from automapgen.nodes.map_mods.template import LuaTemplate, lua_bool, lua_list
from automapgen.nodes.maps import GAME_METERS_PER_PIXEL, MapFormat
from automapgen.nodes.mods import CONTENT, ModFiles
from automapgen.nodes.roads.plan import RoadPlan

SCRIPT_DIR = "dhe_roads"

type Point = tuple[float, float, float]                 # u, v (map fractions), game height
KINDS = ("normal", "bridge", "tunnel")                  # edge kinds; the index is the code in the script


class RoadModSettings(BaseModel):
    """Settings of the road mod (a value of configs/map_mods.toml)."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    road_era: str
    """Street type: key in road_templates ("new" = country road new from 1940, "old" = old until 1940)."""
    road_templates: dict[str, str]
    """Street template per era."""
    bridge_types: tuple[tuple[float, str], ...]
    """Bridge type by length: (longest bridge in game m, end of the type's file name), shortest first."""
    tunnel_type: str
    """End of the tunnel type's file name."""
    auto_start: bool
    """Start building shortly after the game starts (else on the start signal)."""
    start_marker: str
    """Start signal: a street built by the player whose type contains this."""
    max_edge_game_m: float
    """Split longer pieces (the road follows the terrain at the points)."""
    min_edge_game_m: float
    """Merge shorter pieces."""
    remove_buildings: bool
    """Tear down buildings in the way (else rather a gap)."""
    flip_y: bool
    """If the roads lie mirrored at the north-south axis in game."""


@dataclass(frozen=True)
class ScriptWay:
    """A way for the game script: edge kind, structure type (0 = none, else 1-based into the script's list) and its
    nodes (indices into the points)."""

    kind: int
    structure: int
    nodes: Sequence[int]


@dataclass(frozen=True)
class RoadWays:
    """Points (u, v, game height) and ways, ready for the game script; ways share the nodes where they meet."""

    points: Sequence[Point]
    ways: Sequence[ScriptWay]
    length_game_m: float

    @property
    def edge_count(self) -> int:
        return sum(len(w.nodes) - 1 for w in self.ways)

    @property
    def junction_count(self) -> int:
        """Nodes where three or more way ends meet or a way starts on another one."""
        uses: dict[int, int] = {}
        for w in self.ways:
            for i, n in enumerate(w.nodes):
                uses[n] = uses.get(n, 0) + (1 if i in (0, len(w.nodes) - 1) else 2)
        return sum(1 for count in uses.values() if count >= 3)


class RoadEdgeSplitter:
    """Merges too short pieces and splits long ones into equal parts (heights linear in between); the end nodes of
    a way stay, so ways keep meeting where they meet."""

    def __init__(self, min_edge_game_m: float, max_edge_game_m: float, game_w: float, game_h: float,
                 bridge_types: Sequence[tuple[float, str]]) -> None:
        self._min = min_edge_game_m
        self._max = max_edge_game_m
        self._game_w = game_w
        self._game_h = game_h
        self._bridge_types = bridge_types

    def split(self, nodes: Sequence[Sequence[float]], ways: Sequence[Mapping[str, Any]]) -> RoadWays:
        points: list[Point] = []
        shared: dict[int, int] = {}
        script_ways: list[ScriptWay] = []
        total = 0.0

        def end(i: int) -> int:
            if i not in shared:
                points.append(_point(nodes[i]))
                shared[i] = len(points) - 1
            return shared[i]

        for way in ways:
            pts = [_point(nodes[i]) for i in way["nodes"]]
            kept = [pts[0]]
            for p in pts[1:-1]:
                if self._dist(kept[-1], p) >= self._min and self._dist(p, pts[-1]) >= self._min:
                    kept.append(p)
            kept.append(pts[-1])
            length = sum(self._dist(a, b) for a, b in pairwise(kept))
            if len(kept) < 2 or length == 0:
                continue
            ids = [end(way["nodes"][0])]
            for k, (a, b) in enumerate(pairwise(kept)):
                n = max(1, math.ceil(self._dist(a, b) / self._max))
                inner = [_between(a, b, s / n) for s in range(1, n)]
                for q in inner if k == len(kept) - 2 else [*inner, b]:      # the last point is the end node
                    points.append(q)
                    ids.append(len(points) - 1)
            ids.append(end(way["nodes"][-1]))
            kind = KINDS.index(way["kind"])
            script_ways.append(ScriptWay(kind, self._structure(way["kind"], length), ids))
            total += length
        return RoadWays(points, script_ways, total)

    def _structure(self, kind: str, length: float) -> int:
        """1-based index into structure_names (bridges by length, then the tunnel), 0 for normal ways."""
        if kind == "normal":
            return 0
        if kind == "tunnel":
            return len(self._bridge_types) + 1
        return next((i + 1 for i, (longest, _) in enumerate(self._bridge_types) if length <= longest),
                    len(self._bridge_types))

    def _dist(self, a: Point, b: Point) -> float:
        return math.hypot((b[0] - a[0]) * self._game_w, (b[1] - a[1]) * self._game_h)


def _point(node: Sequence[float]) -> Point:
    return float(node[0]), float(node[1]), float(node[2])


def _between(a: Point, b: Point, f: float) -> Point:
    return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f


def game_size(map_format: MapFormat) -> tuple[int, int]:
    """Size of the game map in metres (the heightmap's pixel grid)."""
    return (map_format.width_px - 1) * GAME_METERS_PER_PIXEL, (map_format.height_px - 1) * GAME_METERS_PER_PIXEL


class RoadScript:
    """The game script with the road network."""

    def __init__(self, settings: RoadModSettings) -> None:
        self._settings = settings

    def render(self, roads: RoadWays, aspect: float) -> str:
        """`aspect` = game width / game height of the map."""
        s = self._settings
        template = LuaTemplate.named("roads.script.lua")
        nodes = " ".join(f"{round(u * 1e7)} {round(v * 1e7)} {round(z * 100)}" for u, v, z in roads.points)
        ways = "|".join(" ".join(str(n) for n in (w.kind, w.structure, *(i + 1 for i in w.nodes))) for w in roads.ways)
        structures = [name for _, name in s.bridge_types] + [s.tunnel_type]
        return template.render({
            "VERSION": hashlib.md5((template.text + nodes + ways).encode()).hexdigest()[:12],
            "FLIPY": lua_bool(s.flip_y), "ASPECT": f"{aspect:.6f}", "TEMPLATE": s.road_templates[s.road_era],
            "STRUCTURES": lua_list(structures), "MARKER": s.start_marker, "AUTOSTART": lua_bool(s.auto_start),
            "REMOVE": lua_bool(s.remove_buildings), "NODES": nodes, "WAYS": ways})


class RoadModParams(NodeParams):
    road_mod: RoadModSettings
    """Settings of the road mod."""


@node("mod.roads", output="files")
def road_mod(plan: RoadPlan, map_format: MapFormat, params: RoadModParams, context: NodeContext) -> ModFiles:
    """The content of the road mod: a game script that builds the planned road network in game."""
    s = params.road_mod
    if s.road_era not in s.road_templates:
        raise ValueError(f"road_era '{s.road_era}' has no street template (road_templates: {sorted(s.road_templates)})")
    game_w, game_h = game_size(map_format)
    splitter = RoadEdgeSplitter(s.min_edge_game_m, s.max_edge_game_m, game_w, game_h, s.bridge_types)
    roads = splitter.split(plan.data["nodes"], plan.data["ways"])
    context.log(f"{len(roads.ways)} ways, {roads.edge_count} edges, {roads.junction_count} junctions, "
                f"{roads.length_game_m / 1000:.1f} km in game")
    folder = f"{CONTENT}{SCRIPT_DIR}/{SCRIPT_DIR}"
    return ModFiles.of({
        f"{folder}.script.lua": RoadScript(s).render(roads, game_w / game_h).encode("utf-8"),
        f"{folder}.gs.lua": LuaTemplate.named("roads.gs.lua").text.encode("utf-8")})
