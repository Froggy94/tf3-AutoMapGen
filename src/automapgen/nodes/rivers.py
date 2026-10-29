"""River planning (no terrain change): continuous rivers from OpenStreetMap, split into real and painted water,
widths and weirs - plus a preview and height profiles to check it.

1. Waterways from OSM, drawn in flow direction -> directed network (nodes = OSM points on the map); unnamed ways
   carry the name of their river relation.
2. Valley floor per node: lowest real height within a radius (input `floor`), smoothed along the course.
3. Real water possible where the floor lies at most `real_water_until_m - land_min` above the water line; connected
   pieces shorter than `min_real_run_game_m` do not count. Real water = everything downstream of such a place; its
   level is the water line.
4. Painted water: level follows the valley floor, never rising downstream.
5. Weir: transition painted -> real (drop = painted level - water line).
6. Width per node: real water from the WorldCover water area (min..max); painted riverbed varies slowly in space.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple

import numpy as np
from numpy.typing import NDArray
from PIL import Image as PilImage
from PIL import ImageDraw, ImageFont
from scipy.ndimage import distance_transform_edt, map_coordinates

from automapgen.graph import NodeContext, NodeParams, data_type, node, param
from automapgen.nodes.documents import JsonDocument, JsonDocumentCodec
from automapgen.nodes.images import Image
from automapgen.nodes.maps import GAME_METERS_PER_PIXEL, MapFrame, MapProjection
from automapgen.nodes.osm import OsmWays
from automapgen.nodes.rasters import Field, Mask

REAL_WATER_ABOVE_M = 120.0
"""Without a "real water up to" value: real water up to this much above the lowest land (legacy default)."""


def real_water_until(until_m: float | None, land_min_m: float) -> float:
    """The "real water up to" height a.s.l.; None = `REAL_WATER_ABOVE_M` above the lowest land."""
    return land_min_m + REAL_WATER_ABOVE_M if until_m is None else until_m

_PX = GAME_METERS_PER_PIXEL
_NO_CEMT = ("", "0")                  # CEMT 0: recreational craft only, no cargo ships
_REAL_COLOR = (20, 60, 230)
_NAVIGABLE_COLOR = (130, 0, 200)
_PAINTED_COLOR = (0, 190, 190)
_WEIR_COLOR = (230, 0, 0)
_WATER_COLOR = (150, 185, 215)

type NodeId = int


@data_type("river_plan", "Planned rivers: sections with points (map fractions), width, real/painted, level, weirs.",
           codec=JsonDocumentCodec(lambda data: RiverPlan(data)), preview=lambda p: p.summary())
@dataclass(frozen=True)
class RiverPlan(JsonDocument):
    """The plan as plain data (the format of the `_rivers.json` file):
    {ref_m, real_water_max_m, real_water_until_m, min_width_game_m, real_water_rivers ("all" | "navigable"),
     rivers: [{name, points: [[u, v]], width_game_m: [], real: [], navigable: [], level_m: [], floor_m: []}],
     weirs: [...]}."""

    def summary(self) -> str:
        rivers = self.data["rivers"]
        return (f"{len(rivers)} sections, {len(self.data['weirs'])} weirs, rivers: "
                + ", ".join(sorted({r["name"] for r in rivers if r["name"]})))


def preview_font(size: int = 14, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Arial (bold: Arial Bold), else Pillow's default font."""
    try:
        return ImageFont.truetype("arialbd.ttf" if bold else "arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def hill_shade(dem: NDArray[np.float32], width: int, height: int) -> NDArray[np.uint8]:
    """Grey hill shading of the elevation, resized to width x height (RGB), as background of previews."""
    small = np.asarray(PilImage.fromarray(dem).resize((width, height), PilImage.Resampling.BILINEAR),
                       dtype=np.float32)
    gy, gx = np.gradient(small)
    shade = np.clip(0.75 + (gx - gy) * 0.02, 0.35, 1.0)
    return (np.stack([shade] * 3, axis=2) * 210).astype(np.uint8)


@dataclass
class RiverNetwork:
    """Directed network: nodes in heightmap pixels, edges in flow direction, name per edge, navigable edges (a node
    is navigable when any edge at it is)."""

    pos: dict[NodeId, tuple[float, float]]
    succ: dict[NodeId, set[NodeId]] = field(default_factory=dict)
    pred: dict[NodeId, set[NodeId]] = field(default_factory=dict)
    edge_name: dict[tuple[NodeId, NodeId], str] = field(default_factory=dict)
    navigable_edges: set[tuple[NodeId, NodeId]] = field(default_factory=set)

    @classmethod
    def from_ways(cls, ways: OsmWays, projection: MapProjection, width: int, height: int,
                  navigable: frozenset[int] = frozenset()) -> RiverNetwork:
        """`navigable` = ids of the navigable ways."""
        all_ids = {r: (x, y) for w in ways.ways for r, x, y in w.nodes}
        ids = list(all_ids)
        uvs = projection.ring_to_uv([all_ids[i] for i in ids])
        pos = {}
        for i, (u, v) in zip(ids, uvs, strict=True):
            u, v = u * (width - 1), v * (height - 1)
            if 0 <= u <= width - 1 and 0 <= v <= height - 1:
                pos[i] = (u, v)
        net = cls(pos)
        for w in ways.ways:
            seq = [r for r, _, _ in w.nodes]
            for a, b in itertools.pairwise(seq):
                if a in pos and b in pos and a != b:
                    net.succ.setdefault(a, set()).add(b)
                    net.pred.setdefault(b, set()).add(a)
                    net.edge_name[(a, b)] = w.name
                    if w.id in navigable:
                        net.navigable_edges.add((a, b))
        return net

    def navigable_nodes(self) -> set[NodeId]:
        return {n for edge in self.navigable_edges for n in edge}

    @property
    def nodes(self) -> list[NodeId]:
        return sorted(set(self.succ) | set(self.pred))

    def seg_len(self, a: NodeId, b: NodeId) -> float:
        """Game metres."""
        return math.hypot(self.pos[a][0] - self.pos[b][0], self.pos[a][1] - self.pos[b][1]) * _PX

    def node_name(self, n: NodeId) -> str:
        """Name of the first edge at the node (downstream, else upstream)."""
        for m in self.succ.get(n, ()):
            if self.edge_name.get((n, m)):
                return self.edge_name[(n, m)]
        for p in self.pred.get(n, ()):
            if self.edge_name.get((p, n)):
                return self.edge_name[(p, n)]
        return ""

    def chains(self) -> list[list[NodeId]]:
        """Sections between branches."""
        chains, used = [], set()
        for n in self.nodes:
            pred = self.pred.get(n)
            starts = pred is None or len(pred) != 1 or len(self.succ.get(next(iter(pred)), ())) != 1
            if not starts:
                continue
            for m in self.succ.get(n, ()):
                if (n, m) in used:
                    continue
                chain = [n]
                a, b = n, m
                while True:
                    used.add((a, b))
                    chain.append(b)
                    if len(self.succ.get(b, ())) != 1 or len(self.pred.get(b, ())) != 1:
                        break
                    a, b = b, next(iter(self.succ[b]))
                    if (a, b) in used:
                        break
                chains.append(chain)
        return chains

    def main_path(self, name: str) -> list[NodeId]:
        """Longest path downstream over edges of this name (iterative)."""
        nxt: dict[NodeId, list[NodeId]] = {}
        for (a, b), en in self.edge_name.items():
            if en == name:
                nxt.setdefault(a, []).append(b)
        has_in = {b for v in nxt.values() for b in v}
        order: list[NodeId] = []
        seen: set[NodeId] = set()
        for start in nxt:                          # successors before predecessors (depth first, post order)
            stack = [(start, False)]
            while stack:
                n, done = stack.pop()
                if done:
                    order.append(n)
                    continue
                if n in seen:
                    continue
                seen.add(n)
                stack.append((n, True))
                stack += [(m, False) for m in nxt.get(n, ()) if m not in seen]
        best: dict[NodeId, tuple[float, NodeId | None]] = {}   # node -> (length to the end, next node)
        for n in order:
            candidates = [(self.seg_len(n, m) + best.get(m, (0.0, None))[0], m) for m in nxt.get(n, ())]
            best[n] = max(candidates) if candidates else (0.0, None)
        starts = [n for n in nxt if n not in has_in] or list(nxt)
        if not starts:
            return []
        n = max(starts, key=lambda s: best[s][0])
        path = [n]
        while best.get(n, (0, None))[1] is not None and len(path) < 100000:
            nn = best[n][1]
            assert nn is not None
            n = nn
            path.append(n)
        return path


class _Navigability:
    """Which waterways are navigable: ways with a CEMT class, every way with the name of such a way or a name that
    starts with one of `names` (case-insensitive), and unnamed canals connected to a navigable way (over shared OSM
    points, also over further unnamed canals)."""

    def __init__(self, names: tuple[str, ...]) -> None:
        self._prefixes = tuple(n.casefold() for n in names if n)

    def ways(self, ways: OsmWays) -> frozenset[int]:
        """Ids of the navigable ways."""
        cemt = {w.id for w in ways.ways if w.cemt not in _NO_CEMT}
        cemt_names = {w.name for w in ways.ways if w.id in cemt and w.name}
        navigable = {w.id for w in ways.ways
                     if w.id in cemt or w.name in cemt_names
                     or (w.name and w.name.casefold().startswith(self._prefixes))}
        points = {r for w in ways.ways if w.id in navigable for r, _, _ in w.nodes}
        pending = [w for w in ways.ways if w.kind == "canal" and not w.name and w.id not in navigable]
        added = True
        while added:
            added = False
            for w in [w for w in pending if any(r in points for r, _, _ in w.nodes)]:
                navigable.add(w.id)
                points.update(r for r, _, _ in w.nodes)
                pending.remove(w)
                added = True
        return frozenset(navigable)


@dataclass(frozen=True)
class _Planned:
    network: RiverNetwork
    nodes: list[NodeId]
    floor: NDArray[Any]                   # valley floor per node (smoothed)
    real: NDArray[np.bool_]
    navigable: NDArray[np.bool_]
    level: NDArray[np.float64]
    width: NDArray[np.float64]            # game metres
    weirs: list[dict[str, Any]]
    reference_m: float
    real_water_max_m: float

    @property
    def index(self) -> dict[NodeId, int]:
        return {n: k for k, n in enumerate(self.nodes)}

    @property
    def uv(self) -> NDArray[np.float64]:
        return np.array([self.network.pos[n] for n in self.nodes])


class RiverParams(NodeParams):
    real_water_until_m: float | None = param(None, unit="m")
    """Real water (game water) only up to this height above sea level (the region's "real water up to"); None = 120 m
    above the lowest land. Higher = more real water, but real water is cut down to the water line (deep trenches)."""
    real_water_rivers: Literal["all", "navigable"] = "all"
    """Which waterways may be real water: "all" (below the limit) or only "navigable" ones (CEMT class, a name in
    `navigable_names`, unnamed canals attached to them); other rivers stay painted water, also in flat land."""
    navigable_names: tuple[str, ...] = ()
    """Waterways whose name starts with one of these count as navigable (case-insensitive), e.g.
    ("Dortmund-Ems-Kanal", "Mittellandkanal") where OSM has no CEMT class."""
    min_real_run_game_m: float = param(1000.0, ge=0, unit="game m")
    """Shorter connected pieces where real water is possible do not count (tolerance)."""
    min_width_game_m: float = param(60.0, gt=0, unit="game m")
    """Real water: width at least."""
    max_width_game_m: float = param(300.0, gt=0, unit="game m")
    """Real water: width at most (in lakes the water layer shapes, not the river)."""
    painted_width_game_m: tuple[float, float] = (10.0, 20.0)
    """Painted riverbed: the width varies between these values (game m)."""
    painted_width_wave_game_m: float = param(400.0, gt=0, unit="game m")
    """Painted riverbed: the width varies over about this distance."""
    painted_width_seed: int = 7
    """Random seed of the painted width."""
    floor_smooth_passes: int = param(20, ge=0)
    """Smoothing of the valley floor along the course (mean with the neighbours)."""
    weir_min_drop_m: float = param(2.0, ge=0, unit="m")
    """Smaller steps at the transition painted -> real are not reported as weirs."""
    preview_width: int = param(2000, ge=100)
    """Width of the preview picture in pixels."""
    profile_rivers: int = param(4, ge=0)
    """Height profiles of the rivers with the longest continuous main course."""


class Rivers(NamedTuple):
    plan: RiverPlan
    preview: Image
    profiles: Image


@node("rivers.plan")
def plan(ways: OsmWays, frame: MapFrame, heights: Field, floor: Field, water: Mask, land_min_m: float,
         params: RiverParams, context: NodeContext) -> Rivers:
    """Continuous rivers from OSM: real and painted water, widths, weirs (no terrain change). `heights` = original
    elevation on the heightmap grid, `floor` = valley floor (lowest height around), `water` = WorldCover water (the
    width of real rivers follows it)."""
    until_m = real_water_until(params.real_water_until_m, land_min_m)
    real_water_max_m = until_m - land_min_m
    context.log(f"real water up to {until_m:g} m a.s.l. ({real_water_max_m:g} m above the "
                f"reference height)")
    width, height = frame.width_px, frame.height_px
    network = RiverNetwork.from_ways(ways, MapProjection(frame), width, height,
                                     _Navigability(params.navigable_names).ways(ways))
    water_distance = distance_transform_edt(water.values)   # pixels to the edge of the water area
    planned = _RiverPlanner(params, width, height, context).plan(network, floor.values, water_distance, land_min_m,
                                                                  real_water_max_m)
    preview = _RiverPreview(width, height, params.preview_width).draw(planned, heights.values, water.values)
    profiles, names = _RiverProfiles(params.profile_rivers).draw(planned, heights.values)
    context.log(f"profiles: {', '.join(names)}")
    return Rivers(RiverPlan(_plan_data(planned, params, width, height)), Image(np.asarray(preview)),
                  Image(np.asarray(profiles)))


class _RiverPlanner:
    """Floor, real/painted, levels, weirs and widths for every node of the network."""

    def __init__(self, params: RiverParams, width: int, height: int, context: NodeContext) -> None:
        self._p = params
        self._width = width
        self._height = height
        self._context = context

    def plan(self, net: RiverNetwork, floor: NDArray[np.float32], water_distance: NDArray[np.float64],
             reference_m: float, real_water_max_m: float) -> _Planned:
        p = self._p
        nodes = net.nodes
        idx = {n: k for k, n in enumerate(nodes)}
        uv = np.array([net.pos[n] for n in nodes])
        self._context.log(f"network: {len(nodes)} nodes, {len(net.edge_name)} edges")
        fl = map_coordinates(floor, [uv[:, 1], uv[:, 0]], order=1, mode="nearest")
        nbr = [[idx[m] for m in net.succ.get(n, set()) | net.pred.get(n, set())] for n in nodes]
        for _ in range(p.floor_smooth_passes):
            fl = np.array([(fl[k] + sum(fl[j] for j in nb)) / (1 + len(nb)) for k, nb in enumerate(nbr)])
        navigable_nodes = net.navigable_nodes()
        navigable = np.array([n in navigable_nodes for n in nodes], dtype=bool)
        allowed = navigable if p.real_water_rivers == "navigable" else np.ones(len(nodes), bool)
        real = self._real(net, nodes, idx, nbr, (fl - reference_m <= real_water_max_m) & allowed, allowed)
        level = self._levels(net, nodes, idx, real, fl, reference_m)
        weirs = self._weirs(net, nodes, idx, real, level, reference_m)
        width = np.where(real, self._real_width(uv, water_distance), self._painted_width(uv))
        planned = _Planned(net, nodes, fl, real, navigable, level, width, weirs, reference_m, real_water_max_m)
        self._log(planned)
        return planned

    def _real(self, net: RiverNetwork, nodes: list[NodeId], idx: dict[NodeId, int], nbr: list[list[int]],
              capable: NDArray[np.bool_], allowed: NDArray[np.bool_]) -> NDArray[np.bool_]:
        """Drop "real possible" pieces shorter than min_real_run; real = everything `allowed` downstream of the
        rest."""
        seen = np.zeros(len(nodes), bool)
        for k in range(len(nodes)):
            if not capable[k] or seen[k]:
                continue
            comp, stack, length = [], [k], 0.0
            seen[k] = True
            while stack:
                c = stack.pop()
                comp.append(c)
                n = nodes[c]
                for m in net.succ.get(n, ()):
                    if capable[idx[m]]:
                        length += net.seg_len(n, m)
                for j in nbr[c]:
                    if capable[j] and not seen[j]:
                        seen[j] = True
                        stack.append(j)
            if length < self._p.min_real_run_game_m:
                capable[comp] = False
        real = capable.copy()
        stack = [k for k in range(len(nodes)) if real[k]]
        while stack:
            c = stack.pop()
            for m in net.succ.get(nodes[c], ()):
                j = idx[m]
                if not real[j] and allowed[j]:
                    real[j] = True
                    stack.append(j)
        return real

    @staticmethod
    def _levels(net: RiverNetwork, nodes: list[NodeId], idx: dict[NodeId, int], real: NDArray[np.bool_],
                fl: NDArray[Any], reference_m: float) -> NDArray[np.float64]:
        """Real: reference height; painted: never rising downstream (until stable; the network is nearly always
        acyclic)."""
        level = np.where(real, reference_m, fl).astype(float)
        for _ in range(len(nodes)):
            changed = False
            for k, n in enumerate(nodes):
                if real[k]:
                    continue
                up = [level[idx[p]] for p in net.pred.get(n, ())]
                new = min(fl[k], max(up)) if up else fl[k]
                if new < level[k] - 1e-3:
                    level[k] = new
                    changed = True
            if not changed:
                break
        return level

    def _weirs(self, net: RiverNetwork, nodes: list[NodeId], idx: dict[NodeId, int], real: NDArray[np.bool_],
               level: NDArray[np.float64], reference_m: float) -> list[dict[str, Any]]:
        weirs = []
        for k, n in enumerate(nodes):
            if not real[k]:
                for m in net.succ.get(n, ()):
                    if real[idx[m]] and level[k] - reference_m >= self._p.weir_min_drop_m:
                        weirs.append({"u": net.pos[m][0] / (self._width - 1), "v": net.pos[m][1] / (self._height - 1),
                                      "drop_m": round(float(level[k] - reference_m), 1), "name": net.node_name(n)})
        return weirs

    def _real_width(self, uv: NDArray[np.float64], water_distance: NDArray[np.float64]) -> NDArray[np.float64]:
        p = self._p
        inside = map_coordinates(water_distance, [uv[:, 1], uv[:, 0]], order=0)
        width: NDArray[np.float64] = np.clip(2 * inside * _PX, p.min_width_game_m, p.max_width_game_m)
        return width

    def _painted_width(self, uv: NDArray[np.float64]) -> NDArray[np.float64]:
        """Smooth random field (grid wave / 2, cubic interpolation) -> min..max."""
        p = self._p
        cell = max(p.painted_width_wave_game_m / 2 / _PX, 1)
        noise = np.random.default_rng(p.painted_width_seed).random((int(self._height / cell) + 4,
                                                                    int(self._width / cell) + 4))
        var = np.clip(map_coordinates(noise, [uv[:, 1] / cell + 1, uv[:, 0] / cell + 1], order=3, mode="nearest"),
                      0, 1)
        lo, hi = p.painted_width_game_m
        width: NDArray[np.float64] = lo + var * (hi - lo)
        return width

    def _log(self, planned: _Planned) -> None:
        p, net, real, idx = self._p, planned.network, planned.real, planned.index
        total = sum(net.seg_len(a, b) for a, b in net.edge_name) / 1000
        real_len = sum(net.seg_len(a, b) for a, b in net.edge_name if real[idx[a]] and real[idx[b]]) / 1000
        log = self._context.log
        log(f"river length {total:.0f} km in game, real water {real_len:.0f} km, painted {total - real_len:.0f} km")
        navigable_len = sum(net.seg_len(a, b) for a, b in net.navigable_edges) / 1000
        navigable_names = sorted({net.edge_name[e] for e in net.navigable_edges if net.edge_name.get(e)})
        log(f"navigable waterways {navigable_len:.0f} km in game"
            + (": " + ", ".join(navigable_names) if navigable_names else ""))
        biggest = sorted(planned.weirs, key=lambda w: -w["drop_m"])[:6]
        log(f"weirs (painted -> real): {len(planned.weirs)}" + (", largest steps: " + ", ".join(
            f"{w['name'] or '?'} {w['drop_m']:.0f} m" for w in biggest) if planned.weirs else ""))
        median = np.median(planned.width[real]) if real.any() else 0
        lo, hi = p.painted_width_game_m
        log(f"width of real water: median {median:.0f} m, max {planned.width.max():.0f} m in game (at least "
            f"{p.min_width_game_m:g} m); painted riverbed {lo:g}..{hi:g} m")


def _plan_data(planned: _Planned, params: RiverParams, width: int, height: int) -> dict[str, Any]:
    net, idx, ref = planned.network, planned.index, planned.reference_m
    w1, h1 = width - 1, height - 1
    rivers = [{"name": net.edge_name.get((c[0], c[1]), ""),
               "points": [[round(net.pos[n][0] / w1, 6), round(net.pos[n][1] / h1, 6)] for n in c],
               "width_game_m": [round(float(planned.width[idx[n]]), 1) for n in c],
               "real": [bool(planned.real[idx[n]]) for n in c],
               "navigable": [bool(planned.navigable[idx[n]]) for n in c],
               "level_m": [round(float(planned.level[idx[n]]), 1) for n in c],
               "floor_m": [round(float(planned.floor[idx[n]]), 1) for n in c]} for c in net.chains()]
    real_max = planned.real_water_max_m
    return {"ref_m": ref, "real_water_max_m": real_max, "real_water_until_m": ref + real_max,
            "min_width_game_m": params.min_width_game_m, "real_water_rivers": params.real_water_rivers,
            "rivers": rivers, "weirs": planned.weirs}


class _RiverPreview:
    """Hill shading, real water blue (navigable purple), painted turquoise (width to scale), weirs red, WorldCover
    water light blue."""

    def __init__(self, width: int, height: int, preview_width: int) -> None:
        self._width = width
        self._height = height
        self._pw = preview_width

    def draw(self, planned: _Planned, dem: NDArray[np.float32], water: NDArray[np.bool_]) -> PilImage.Image:
        pw = self._pw
        ph = round(pw * self._height / self._width)
        arr = hill_shade(dem, pw, ph)
        wsmall = np.asarray(PilImage.fromarray(water.astype(np.uint8) * 255).resize(
            (pw, ph), PilImage.Resampling.BILINEAR)) > 100
        arr[wsmall] = _WATER_COLOR
        img = PilImage.fromarray(arr)
        d = ImageDraw.Draw(img)
        font, font_b = preview_font(14), preview_font(16, bold=True)
        f = pw / (self._width - 1)
        idx, uv, real, width = planned.index, planned.uv, planned.real, planned.width
        navigable = planned.network.navigable_edges
        for (n, m) in planned.network.edge_name:
            a, b = idx[n], idx[m]
            col = ((_NAVIGABLE_COLOR if (n, m) in navigable else _REAL_COLOR) if real[a] and real[b]
                   else _PAINTED_COLOR)
            wd = max(2, round((width[a] + width[b]) / 2 / _PX * f))
            d.line([(uv[a, 0] * f, uv[a, 1] * f), (uv[b, 0] * f, uv[b, 1] * f)], fill=col, width=wd)
        for w in planned.weirs:
            x, y = w["u"] * pw, w["v"] * ph
            d.rectangle((x - 5, y - 5, x + 5, y + 5), fill=_WEIR_COLOR, outline=(255, 255, 255))
            d.text((x + 7, y - 8), f"{w['name']} {w['drop_m']:.0f} m", fill=(150, 0, 0), font=font, stroke_width=2,
                   stroke_fill=(255, 255, 255))
        for k, (col, txt) in enumerate([(_REAL_COLOR, "real water (level = reference height)"),
                                        (_NAVIGABLE_COLOR, "real water, navigable waterway"),
                                        (_PAINTED_COLOR, "painted (level follows the valley)"),
                                        (_WEIR_COLOR, "weir (step painted -> real)"),
                                        (_WATER_COLOR, "existing water area (WorldCover)")]):
            d.rectangle((10, 10 + 22 * k, 28, 24 + 22 * k), fill=col, outline=(0, 0, 0))
            d.text((34, 9 + 22 * k), txt, fill=(0, 0, 0), font=font_b, stroke_width=2, stroke_fill=(255, 255, 255))
        return img


class _RiverProfiles:
    """Height profiles of the rivers with the longest continuous main course."""

    def __init__(self, count: int) -> None:
        self._count = count

    def draw(self, planned: _Planned, dem: NDArray[np.float32]) -> tuple[PilImage.Image, list[str]]:
        net, idx, uv, ref = planned.network, planned.index, planned.uv, planned.reference_m
        names = {nm for nm in net.edge_name.values() if nm}
        paths = {nm: net.main_path(nm) for nm in names}
        length = {nm: sum(net.seg_len(a, b) for a, b in itertools.pairwise(pt)) for nm, pt in paths.items()}
        top = sorted(length, key=lambda n: -length[n])[:self._count]
        img = PilImage.new("RGB", (1600, 260 * max(len(top), 1)), (255, 255, 255))
        d = ImageDraw.Draw(img)
        font = preview_font(14)
        for r, nm in enumerate(top):
            self._profile(d, font, planned, dem, r, nm, paths[nm], idx, uv, ref)
        return img, top

    @staticmethod
    def _profile(d: ImageDraw.ImageDraw, font: Any, planned: _Planned, dem: NDArray[np.float32], r: int, nm: str,
                 path: Sequence[NodeId], idx: dict[NodeId, int], uv: NDArray[np.float64], ref: float) -> None:
        net = planned.network
        if len(path) < 2:
            return
        s = np.concatenate([[0], np.cumsum([net.seg_len(a, b) for a, b in itertools.pairwise(path)])])
        s = s / 1000
        k = np.array([idx[n] for n in path])
        t = map_coordinates(dem, [uv[k, 1], uv[k, 0]], order=1)
        lo, hi = min(ref - 20, planned.floor[k].min()), max(t.max(), planned.level[k].max()) + 20
        x0, y0, x1, y1 = 70, 260 * r + 30, 1580, 260 * r + 240

        def px(v: float) -> float:
            return float(x0 + v / max(s[-1], 1e-6) * (x1 - x0))

        def py(v: float) -> float:
            return float(y1 - (v - lo) / (hi - lo) * (y1 - y0))

        d.rectangle((x0, y0, x1, y1), outline=(180, 180, 180))
        d.line([(x0, py(ref)), (x1, py(ref))], fill=(230, 0, 0), width=1)
        d.line([(x0, py(ref + planned.real_water_max_m)), (x1, py(ref + planned.real_water_max_m))],
               fill=(255, 150, 150), width=1)
        d.line([(px(a), py(b)) for a, b in zip(s, t, strict=True)], fill=(170, 170, 170), width=1)
        d.line([(px(a), py(b)) for a, b in zip(s, planned.floor[k], strict=True)], fill=(110, 80, 40), width=2)
        for a in range(len(path) - 1):
            col = _REAL_COLOR if planned.real[k[a]] and planned.real[k[a + 1]] else _PAINTED_COLOR
            d.line([(px(s[a]), py(planned.level[k[a]])), (px(s[a + 1]), py(planned.level[k[a + 1]]))], fill=col,
                   width=3)
        d.text((x0, y0 - 22), f"{nm}: {s[-1]:.1f} km in game (flow left -> right) - grey terrain, brown valley "
                              f"floor, blue/turquoise level real/painted, red reference height {ref:g} m, pink limit "
                              f"of real water", fill=(0, 0, 0), font=font)
        for v in np.linspace(lo, hi, 5):
            d.text((5, py(v) - 7), f"{v:.0f} m", fill=(80, 80, 80), font=font)
