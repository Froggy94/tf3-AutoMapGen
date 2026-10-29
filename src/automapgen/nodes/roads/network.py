"""Road network between the planned towns and industries, fitted into the terrain (ADR 0016).

1. Links: Gabriel graph of the towns (natural neighbours) without detour links and too long links; every industry
   gets a spur to the nearest road or town.
2. Routes, coarse to fine: first on a coarse grid of the whole map (`coarse`: mean values, serpentines allowed as
   a longer line), then on the heading lattice (`lattice`) only in a corridor around the coarse route - wider only
   if no route fits: minimum radius, grade cost, land cover, bridges and tunnels as straight structure moves.
   Other towns and industries are kept clear, planned routes become cheaper (they bundle; shared stretches are
   built once).
3. Sections between junctions, towns and industries; roads end at the town edge and in front of the industry.
4. Per section: arcs (`alignment`), longitudinal profile (`profile`): bridges and tunnels only on the straight
   structure legs and over the wet zone, as straight spans at a constant grade with fitted approaches; the same
   height at every junction; ways split where the kind changes.
5. The terrain under the normal ways is shaped to the profile (like `roads.terrain`); bridges and tunnels leave it.
Positions in game metres from the top-left map corner (x right, y down).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

import numpy as np
from numpy.typing import NDArray
from PIL import Image as PilImage
from PIL import ImageDraw
from scipy.ndimage import distance_transform_edt, map_coordinates
from scipy.spatial import Delaunay

from automapgen.graph import NodeContext, NodeParams, node, param
from automapgen.nodes.images import Image
from automapgen.nodes.industries import IndustryList
from automapgen.nodes.lines import FULL_WEIGHT, LineStamp
from automapgen.nodes.maps import GAME_METERS_PER_PIXEL, MapFormat
from automapgen.nodes.rasters import Field, Mask, resize_floats
from automapgen.nodes.rivers import preview_font
from automapgen.nodes.roads.alignment import Alignment, AlignmentBuilder, Leg
from automapgen.nodes.roads.coarse import CoarseGraph, corridor
from automapgen.nodes.roads.lattice import LatticeCosts, LatticeGraph, Step, Terrain
from automapgen.nodes.roads.plan import RoadPlan
from automapgen.nodes.roads.profile import BRIDGE, KIND_NAMES, NORMAL, TUNNEL, Profile, ProfilePlanner, ProfileRules
from automapgen.nodes.towns import TownList

type Cell = tuple[int, int]
type Log = Callable[[str], None]
type FloatMap = NDArray[np.float64]


class RoadNetworkParams(NodeParams):
    min_capacity: int = param(0, ge=0)
    """Towns with a smaller start size get no road."""
    max_link_game_m: float = param(16000.0, gt=0, unit="game m")
    """Longer links between towns are not planned."""
    detour_via_factor: float = param(1.2, ge=1)
    """Link A-B is dropped if A-C-B is at most this much longer."""
    max_route_detour: float = param(2.5, ge=1)
    """Route at most this much longer than the straight line, else no road."""
    connect_industries: bool = True
    """Every industry gets a spur to the nearest road or town."""
    max_spur_game_m: float = param(2500.0, gt=0, unit="game m")
    """Industries farther from a road or town get no spur."""
    town_radius_game_m: float = param(150.0, ge=0, unit="game m")
    """Roads end this far from the town centre (the town's own streets take over)."""
    industry_clearance_game_m: float = param(80.0, ge=0, unit="game m")
    """Spurs end this far from the industry."""
    cell_game_m: float = param(25.0, gt=0, unit="game m")
    """Grid cell of the fine route search."""
    coarse_cell_game_m: float = param(100.0, gt=0, unit="game m")
    """Grid cell of the coarse route search over the whole map."""
    serpentine_factor: float = param(2.0, ge=1)
    """Coarse search: mean grades up to this many times max_grade (the fine route winds up in serpentines)."""
    corridor_game_m: float = param(150.0, gt=0, unit="game m")
    """The fine route stays this close to the coarse route ..."""
    corridor_widenings: int = param(2, ge=0)
    """... and the corridor is doubled this often if no route fits."""
    min_radius_game_m: float = param(25.0, gt=0, unit="game m")
    """Smallest curve radius."""
    max_grade: float = param(0.2, gt=0)
    """Steepest grade (height / distance)."""
    slope_weight: float = 2.0
    """Surcharge for the grade (at max_grade: cost x 1 + slope_weight)."""
    climb_weight: float = param(3.0, ge=0)
    """Cost per metre of height gained or lost (in metres of road): steady grades and a curve more instead of up
    and down."""
    turn_weight: float = 0.3
    """Surcharge per radian of turning (in cells of length); keeps the line calm."""
    forest_cost: float = 0.3
    """Surcharge per forest share."""
    rock_cost: float = 1.0
    """Surcharge per rock share."""
    glacier_limit: float = 0.5
    """Cells with more glacier are blocked."""
    wet_margin_game_m: float = param(50.0, ge=0, unit="game m")
    """Water (painted and real), shore and riverbeds plus this margin form the wet zone: crossed by bridges only."""
    wet_clearance_game_m: float = param(10.0, ge=0, unit="game m")
    """Bridges lie at least this high above water and riverbeds (real water: above its surface)."""
    valley_game_m: float = param(150.0, ge=0, unit="game m")
    """Near the wet zone the ground costs more (rather higher up the slope than down by the river) ..."""
    valley_cost: float = 1.0
    """... up to this surcharge at the zone, falling to 0 at valley_game_m."""
    reuse_factor: float = param(0.3, gt=0, le=1)
    """Planned roads cost only this much (bundling)."""
    max_structure_game_m: float = param(400.0, ge=0, unit="game m")
    """Longest straight structure move of the route search (bridge, tunnel, fill or cut)."""
    bridge_cost: float = 10.0
    """Surcharge per metre of bridge ..."""
    bridge_height_cost: float = param(0.5, ge=0)
    """... plus this per metre of bridge and metre of height above the ground (high bridges cost more)."""
    fill_cost: float = param(0.2, ge=0)
    """Surcharge per metre of fill and metre of height (below bridge_from_game_m; cheaper than a bridge)."""
    cut_cost: float = param(0.25, ge=0)
    """Surcharge per metre of cut and metre of depth (above tunnel_from_game_m; cheaper than a tunnel)."""
    tunnel_cost: float = 12.0
    """Surcharge per metre of tunnel."""
    structure_fixed_game_m: float = param(300.0, ge=0, unit="game m")
    """Fixed cost of a bridge or tunnel (abutments, portals) as metres of road."""
    structure_max_grade: float = param(0.06, gt=0)
    """Bridges and tunnels at most this steep (they cross; the road climbs on the ground)."""
    straighten_game_m: float = param(10.0, ge=0, unit="game m")
    """Straighten the zigzag of the grid up to this deviation."""
    sample_game_m: float = param(4.0, gt=0, unit="game m")
    """Sample the line this finely (profile, terrain)."""
    node_game_m: float = param(12.0, gt=0, unit="game m")
    """Distance of the plan's nodes."""
    profile_smooth_game_m: float = param(120.0, ge=0, unit="game m")
    """Smooth the longitudinal profile of the normal road (long = steady grades; fills and cuts take the rest)."""
    approach_smooth_game_m: float = param(30.0, ge=0, unit="game m")
    """Smooth the approaches to bridges and tunnels."""
    bridge_from_game_m: float = param(10.0, gt=0, unit="game m")
    """Bridge where the road lies this much above the ground; below a fill."""
    tunnel_from_game_m: float = param(12.0, gt=0, unit="game m")
    """Tunnel where the road lies this much below the ground; above a cut."""
    min_bridge_game_m: float = param(30.0, ge=0, unit="game m")
    """Shorter bridges become fills (not over water)."""
    min_tunnel_game_m: float = param(150.0, ge=0, unit="game m")
    """Shorter tunnels become cuts."""
    max_span_extend_game_m: float = param(60.0, ge=0, unit="game m")
    """A bridge or tunnel span grows at most this far beyond its leg of the route search (to the rim of a gorge:
    the legs end on the cells of the search grid)."""
    max_structure_gap_game_m: float = param(40.0, ge=0, unit="game m")
    """Normal pieces between two bridges (tunnels) up to this length become part of them."""
    end_blend_game_m: float = param(60.0, ge=0, unit="game m")
    """At junctions and ends the profile meets the ground height over this length."""
    width_game_m: float = param(14.0, gt=0, unit="game m")
    """Width of the levelled strip under the road (carriageway and walkways)."""
    bank_slope: float = param(0.6, gt=0)
    """Bank slope (height / width) of the terrain beside the road."""
    blend_min_px: float = param(1.5, gt=0)
    """Transition to the side at least (heightmap pixels)."""
    preview_width: int = param(2000, ge=100)
    """Width of the preview picture in pixels."""


@dataclass(frozen=True)
class Target:
    name: str
    kind: str                              # "town" or "industry"
    x: float
    y: float
    radius: float                          # the road ends this far from the point
    size: int


@dataclass
class _Routes:
    """The routed network on the search grid: edges between cells, structure edges, the cells per target."""

    edges: dict[tuple[Cell, Cell], bool] = field(default_factory=dict)    # (a, b) with a < b -> structure
    target_cells: dict[Cell, int] = field(default_factory=dict)
    failed: list[tuple[int, int, str]] = field(default_factory=list)

    def add(self, steps: Sequence[Step]) -> None:
        for s in steps:
            key = (min(s.start, s.end), max(s.start, s.end))
            self.edges[key] = self.edges.get(key, False) or s.structure

    @property
    def cells(self) -> set[Cell]:
        return {c for pair in self.edges for c in pair}


class RoadNetwork(NamedTuple):
    plan: RoadPlan
    heights: Field
    core: Mask
    preview: Image


@node("roads.network")
def network(heights: Field, water: Mask, riverbed: Mask, forest: Mask, rock: Mask, glacier: Mask,
            towns: TownList, industries: IndustryList, map_format: MapFormat, land_min_m: float, base: Image,
            params: RoadNetworkParams, context: NodeContext) -> RoadNetwork:
    """Roads between the towns and to the industries through the terrain (`heights`, real m on the heightmap grid;
    `water` = painted and real water with shore, `riverbed`), with bridges and tunnels; water and riverbeds with a
    margin are crossed by bridges only. Node heights in game metres above `land_min_m`. The terrain under the normal
    ways is shaped (`heights`, `core` = pixels at full weight); the preview draws the network on `base`."""
    p = params
    wet = _WetZone.of(water, riverbed, p)
    grid = _SearchGrid.of(heights, wet, forest, rock, glacier, map_format, land_min_m, p, p.cell_game_m, 0.0)
    coarse = _SearchGrid.of(heights, wet, forest, rock, glacier, map_format, land_min_m, p, p.coarse_cell_game_m,
                            0.9)                       # optimistic: the fine search keeps to the wet zone exactly
    targets = _targets(towns, industries, grid, p)
    context.log(f"map {grid.width_m / 1000:.1f} x {grid.height_m / 1000:.1f} km in game, search grid "
                f"{grid.cols} x {grid.rows}; {sum(t.kind == 'town' for t in targets)} towns, "
                f"{sum(t.kind == 'industry' for t in targets)} industries")
    routes = _Router(grid, coarse, targets, p, context.log).route()
    sections = _SectionBuilder(grid, targets, routes, p).sections()
    plan, samples = _PlanWriter(grid, targets, p).plan(sections)
    shaped, core = _TerrainShaper(grid, p).shape(heights, wet.core, samples)
    structures = {kind: sum(1 for w in plan.data["ways"] if w["kind"] == kind) for kind in KIND_NAMES}
    context.log(f"network: {len(sections)} sections, {sum(s.alignment.length_m for s in sections) / 1000:.1f} km, "
                f"{len(plan.data['ways'])} ways ({structures['bridge']} bridges, {structures['tunnel']} tunnels), "
                f"{len(routes.failed)} links without a road; normal road in the wet zone: "
                f"{_normal_in_wet_zone(grid, sections):.0f} m")
    preview = _preview(base, grid, targets, sections, routes, p.preview_width)
    return RoadNetwork(plan, shaped, core, Image(np.asarray(preview)))


def _normal_in_wet_zone(grid: _SearchGrid, sections: Sequence[_Section]) -> float:
    """Length of normal road inside the wet zone (should be 0; a check for the log)."""
    total = 0.0
    for s in sections:
        pts = s.alignment.points
        zone, _ = grid.wet_at(pts[:, 0], pts[:, 1])
        wet_normal = (zone[:-1] | zone[1:]) & (s.profile.kinds == NORMAL)
        total += float(np.hypot(*np.diff(pts, axis=0).T)[wet_normal].sum())
    return total


# --- the search grid ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _WetZone:
    """On the heightmap grid: water and riverbeds (`core`), with the margin (`zone`, crossed by bridges only) and the
    surcharge near the zone (`valley`, 0..1)."""

    core: NDArray[np.bool_]
    zone: NDArray[np.bool_]
    valley: FloatMap

    @classmethod
    def of(cls, water: Mask, riverbed: Mask, p: RoadNetworkParams) -> _WetZone:
        core = water.values.astype(bool) | riverbed.values.astype(bool)
        if not core.any():
            none = np.zeros(core.shape, dtype=bool)
            return cls(core, none, np.zeros(core.shape))
        distance_m = distance_transform_edt(~core) * GAME_METERS_PER_PIXEL
        zone = distance_m <= p.wet_margin_game_m
        beyond = distance_m - p.wet_margin_game_m
        valley = np.clip(1 - beyond / max(p.valley_game_m, 1e-6), 0, 1) * ~zone
        return cls(core, np.asarray(zone), np.asarray(valley, dtype=float))


@dataclass(frozen=True)
class _SearchGrid:
    """The map on the search grid (game metres, heights above the reference) and the fine heightmap grid."""

    rows: int
    cols: int
    cell_m: float
    width_m: float
    height_m: float
    height: FloatMap
    factor: FloatMap
    blocked: NDArray[np.bool_]
    water: NDArray[np.bool_]               # the wet zone: crossed by bridges only
    fine_height: FloatMap                  # game metres above the reference, heightmap grid
    fine_wet: _WetZone
    land_min_m: float
    height_game_per_real: float

    @classmethod
    def of(cls, heights: Field, wet: _WetZone, forest: Mask, rock: Mask, glacier: Mask, map_format: MapFormat,
           land_min_m: float, p: RoadNetworkParams, cell_m: float, wet_share: float) -> _SearchGrid:
        """`wet_share`: cells with a larger share of the wet zone are crossed by bridges only; below it the share
        costs like bridge metres."""
        fine_rows, fine_cols = heights.values.shape
        width_m, height_m = (fine_cols - 1) * GAME_METERS_PER_PIXEL, (fine_rows - 1) * GAME_METERS_PER_PIXEL
        cols, rows = max(2, round(width_m / cell_m)), max(2, round(height_m / cell_m))

        def share(values: NDArray[Any]) -> FloatMap:
            return resize_floats(values.astype(np.float32), cols, rows, PilImage.Resampling.BOX).astype(float)

        gpr = map_format.height_game_per_real
        fine = (heights.values.astype(np.float64) - land_min_m) * gpr
        height = resize_floats(fine, cols, rows, PilImage.Resampling.BOX).astype(float)
        zone = share(wet.zone)
        crossing = zone > wet_share
        factor = (1 + p.forest_cost * share(forest.values) + p.rock_cost * share(rock.values)
                  + p.valley_cost * share(wet.valley) + p.bridge_cost * zone * ~crossing)
        far = distance_transform_edt(crossing) * cell_m > p.max_structure_game_m / 2        # open water
        blocked = (share(glacier.values) > p.glacier_limit) | np.asarray(far)
        return cls(rows, cols, width_m / cols, width_m, height_m, height, factor, blocked, crossing, fine, wet,
                   land_min_m, gpr)

    def cell_of(self, x: float, y: float) -> Cell:
        return (min(self.rows - 1, max(0, int(y / self.height_m * self.rows))),
                min(self.cols - 1, max(0, int(x / self.width_m * self.cols))))

    def terrain(self) -> Terrain:
        return Terrain(self.height, self.factor, self.blocked, self.water)

    def centre(self, cell: Cell) -> tuple[float, float]:
        return (cell[1] + 0.5) * self.width_m / self.cols, (cell[0] + 0.5) * self.height_m / self.rows

    def ground(self, x: NDArray[np.float64], y: NDArray[np.float64]) -> FloatMap:
        """Game height of the fine grid at the points (bilinear)."""
        rows, cols = self.fine_height.shape
        result: FloatMap = map_coordinates(self.fine_height, [y / self.height_m * (rows - 1),
                                                              x / self.width_m * (cols - 1)], order=1, mode="nearest")
        return result

    def wet_at(self, x: NDArray[np.float64], y: NDArray[np.float64]) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
        """At the points: in the wet zone, on water or riverbed itself."""
        rows, cols = self.fine_wet.core.shape
        r = np.clip(np.round(y / self.height_m * (rows - 1)).astype(int), 0, rows - 1)
        c = np.clip(np.round(x / self.width_m * (cols - 1)).astype(int), 0, cols - 1)
        return self.fine_wet.zone[r, c], self.fine_wet.core[r, c]


def _targets(towns: TownList, industries: IndustryList, grid: _SearchGrid, p: RoadNetworkParams) -> list[Target]:
    """Towns (largest first) and industries in game metres from the top-left corner."""
    out = [Target(t["name"], "town", t["x"] + grid.width_m / 2, grid.height_m / 2 - t["y"], p.town_radius_game_m,
                  int(t["capacity"])) for t in towns.data if int(t["capacity"]) >= p.min_capacity]
    out.sort(key=lambda t: -t.size)
    if p.connect_industries:
        out += [Target(i["name"], "industry", i["x"] + grid.width_m / 2, grid.height_m / 2 - i["y"],
                       p.industry_clearance_game_m, 0) for i in industries.data]
    return [t for t in out if 0 <= t.x <= grid.width_m and 0 <= t.y <= grid.height_m]


# --- routes --------------------------------------------------------------------------------------------------------


class _Router:
    """Links between the towns, spurs to the industries: coarse route over the whole map, fine route in a corridor
    around it."""

    def __init__(self, grid: _SearchGrid, coarse: _SearchGrid, targets: Sequence[Target], params: RoadNetworkParams,
                 log: Log) -> None:
        self._g = grid
        self._c = coarse
        self._targets = targets
        self._p = params
        self._log = log
        self._costs = self._lattice_costs(grid.cell_m, (2, 3, 4, 6, 8, 11, 16))
        self._coarse = CoarseGraph(coarse.terrain(), self._lattice_costs(coarse.cell_m, (2, 3, 4)),
                                   params.serpentine_factor, params.reuse_factor)
        self._routes = _Routes()
        self._town_cells = {i: grid.cell_of(t.x, t.y) for i, t in enumerate(targets) if t.kind == "town"}
        self._target_area = self._discs()

    def _lattice_costs(self, cell_m: float, structure_steps: tuple[int, ...]) -> LatticeCosts:
        p = self._p
        return LatticeCosts(cell_m, p.min_radius_game_m, p.max_grade, p.slope_weight, p.turn_weight,
                            structure_steps, p.max_structure_game_m, p.bridge_cost, p.tunnel_cost,
                            p.structure_fixed_game_m, p.climb_weight, p.bridge_from_game_m, p.tunnel_from_game_m,
                            p.bridge_height_cost, p.fill_cost, p.cut_cost, p.structure_max_grade,
                            p.min_tunnel_game_m)

    def route(self) -> _Routes:
        towns = [i for i, t in enumerate(self._targets) if t.kind == "town"]
        links = self._links(towns)
        for i, j in links:
            self._link(i, j)
        spurs = [i for i, t in enumerate(self._targets) if t.kind == "industry"]
        for i in spurs:
            self._spur(i)
        r = self._routes
        self._log(f"links: {len(links)} planned, {len(r.failed)} without a road" + "".join(
            f"; {self._targets[i].name} - {self._targets[j].name if j >= 0 else 'network'}: {why}"
            for i, j, why in r.failed))
        return r

    def _links(self, towns: Sequence[int]) -> list[tuple[int, int]]:
        """Gabriel graph of the towns without detour links and too long links."""
        if len(towns) < 2:
            return []
        points = np.array([[self._targets[i].x, self._targets[i].y] for i in towns])
        dist = np.hypot(*(points[:, None, :] - points[None, :, :]).transpose(2, 0, 1))
        pairs: set[tuple[int, int]] = set()
        if len(towns) == 2:
            pairs.add((0, 1))
        else:
            for simplex in Delaunay(points).simplices:
                for a in range(3):
                    i, j = sorted((int(simplex[a]), int(simplex[(a + 1) % 3])))
                    pairs.add((i, j))
        links = []
        for i, j in sorted(pairs):
            if dist[i, j] > self._p.max_link_game_m:
                continue
            others = [k for k in range(len(points)) if k not in (i, j)]
            mid, r = (points[i] + points[j]) / 2, dist[i, j] / 2
            if others and np.any(np.hypot(*(points[others] - mid).T) < r):
                continue
            if others and np.any(dist[i, others] + dist[others, j] < self._p.detour_via_factor * dist[i, j]):
                continue
            links.append((towns[i], towns[j]))
        return sorted(links, key=lambda ij: -(self._targets[ij[0]].size + self._targets[ij[1]].size))

    def _discs(self) -> NDArray[np.int32]:
        """Per cell the town or industry whose area it lies in (-1 = none); other routes keep clear of it."""
        g = self._g
        disc = np.full((g.rows, g.cols), -1, dtype=np.int32)
        rr, cc = np.mgrid[0: g.rows, 0: g.cols]
        for i, t in enumerate(self._targets):
            disc[np.hypot((cc + 0.5) * g.cell_m - t.x, (rr + 0.5) * g.cell_m - t.y) < t.radius] = i
        return disc

    def _window(self, points: Sequence[tuple[float, float]], margin: float) -> tuple[slice, slice]:
        g = self._g
        xs, ys = [x for x, _ in points], [y for _, y in points]
        r0, c0 = g.cell_of(min(xs) - margin, min(ys) - margin)
        r1, c1 = g.cell_of(max(xs) + margin, max(ys) + margin)
        return slice(r0, r1 + 1), slice(c0, c1 + 1)

    def _terrain(self, rows: slice, cols: slice, own: set[int]) -> Terrain:
        g = self._g
        factor = g.factor[rows, cols].copy()
        for (a, b), _ in self._routes.edges.items():
            for r, c in (a, b):
                if rows.start <= r < rows.stop and cols.start <= c < cols.stop:
                    factor[r - rows.start, c - cols.start] *= self._p.reuse_factor
        disc = self._target_area[rows, cols]
        blocked = g.blocked[rows, cols] | ((disc >= 0) & ~np.isin(disc, list(own)))
        return Terrain(g.height[rows, cols], factor, blocked, g.water[rows, cols])

    def _link(self, i: int, j: int) -> None:
        a, b = self._targets[i], self._targets[j]
        straight = math.hypot(b.x - a.x, b.y - a.y)
        result = self._coarse_to_fine((a.x, a.y), [(b.x, b.y)], {i, j}, [self._town_cells[j]])
        if isinstance(result, str):
            self._routes.failed.append((i, j, result))
            return
        steps, length = result
        if length > self._p.max_route_detour * straight:
            self._routes.failed.append((i, j, f"detour x{length / straight:.1f}"))
            return
        self._accept(steps, [i, j])

    def _spur(self, i: int) -> None:
        t = self._targets[i]
        reach = self._p.max_spur_game_m
        on_roads = {c for c in self._routes.cells if not self._in_target_area(c)}    # trimmed away there
        goals = [c for c in on_roads | set(self._town_cells.values())
                 if math.dist(self._g.centre(c), (t.x, t.y)) <= reach]
        if not goals:
            self._routes.failed.append((i, -1, "no road near"))
            return
        own = {i} | {k for k, cell in self._town_cells.items() if cell in goals}
        result = self._coarse_to_fine((t.x, t.y), [self._g.centre(c) for c in goals], own, goals)
        if isinstance(result, str):
            self._routes.failed.append((i, -1, result))
            return
        self._accept(result[0], [i])

    def _coarse_to_fine(self, start: tuple[float, float], ends: Sequence[tuple[float, float]], own: set[int],
                        goals: Sequence[Cell]) -> tuple[list[Step], float] | str:
        """Coarse route from `start` to the nearest of `ends`, then the fine route to the nearest goal cell in a
        corridor around it, widened if no route fits; the reason as text if there is none."""
        c = self._c
        coarse = self._coarse.route(c.cell_of(*start), [c.cell_of(x, y) for x, y in ends])
        if coarse is None:
            return "no coarse path"
        line = [start, *(c.centre(cell) for cell in coarse[1:-1]), self._nearest(ends, c.centre(coarse[-1]))]
        width = self._p.corridor_game_m
        for _ in range(self._p.corridor_widenings + 1):
            rows, cols = self._window(line, width + 2 * self._g.cell_m)
            inside = corridor(line, (rows.stop - rows.start, cols.stop - cols.start),
                              (cols.start * self._g.cell_m, rows.start * self._g.cell_m), self._g.cell_m, width)
            local_goals = [g for g in goals if rows.start <= g[0] < rows.stop and cols.start <= g[1] < cols.stop
                           and inside[g[0] - rows.start, g[1] - cols.start]]
            if local_goals:
                result = self._search(rows, cols, own, self._g.cell_of(*start), local_goals, inside)
                if result is not None:
                    return result
            width *= 2
        return "no path in the corridor"

    @staticmethod
    def _nearest(points: Sequence[tuple[float, float]], to: tuple[float, float]) -> tuple[float, float]:
        return min(points, key=lambda q: math.dist(q, to))

    def _in_target_area(self, cell: Cell) -> bool:
        """Inside a town or industry area or close to it (a junction there would be cut off with the end)."""
        x, y = self._g.centre(cell)
        margin = 3 * self._g.cell_m
        return any(math.hypot(t.x - x, t.y - y) < t.radius + margin for t in self._targets)

    def _search(self, rows: slice, cols: slice, own: set[int], start: Cell, goals: Sequence[Cell],
                inside: NDArray[np.bool_]) -> tuple[list[Step], float] | None:
        terrain = self._terrain(rows, cols, own)
        own_area = np.isin(self._target_area[rows, cols], list(own))       # the road is cut off there anyway
        terrain = Terrain(terrain.height, terrain.factor, terrain.blocked | ~inside,
                          terrain.crossing_only & ~own_area)
        local = LatticeGraph(_freed(terrain, [_local(start, rows, cols), *(_local(g, rows, cols) for g in goals)]),
                             self._costs)
        result = local.route(_local(start, rows, cols), [_local(g, rows, cols) for g in goals])
        if result is None:
            return None
        steps, _ = result
        steps = [Step(_global(s.start, rows, cols), _global(s.end, rows, cols), s.structure) for s in steps]
        length = sum(math.dist(self._g.centre(s.start), self._g.centre(s.end)) for s in steps)
        return steps, length

    def _accept(self, steps: Sequence[Step], ends: Sequence[int]) -> None:
        self._routes.add(steps)
        self._coarse.mark_used({self._c.cell_of(*self._g.centre(cell)) for s in steps for cell in (s.start, s.end)})
        for k in ends:
            self._routes.target_cells[self._g.cell_of(self._targets[k].x, self._targets[k].y)] = k


def _local(cell: Cell, rows: slice, cols: slice) -> Cell:
    return cell[0] - rows.start, cell[1] - cols.start


def _global(cell: Cell, rows: slice, cols: slice) -> Cell:
    return cell[0] + rows.start, cell[1] + cols.start


def _freed(terrain: Terrain, cells: Sequence[Cell]) -> Terrain:
    """Start and goal cells are never blocked (a town on a glacier edge, an industry by the water)."""
    blocked, crossing = terrain.blocked.copy(), terrain.crossing_only.copy()
    for r, c in cells:
        blocked[r, c] = crossing[r, c] = False
    return Terrain(terrain.height, terrain.factor, blocked, crossing)


# --- sections ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Section:
    """A road between two junctions, towns or industries: its line, profile and the keys of its end nodes (the
    junction cell, or None for an end in front of a town or industry)."""

    alignment: Alignment
    profile: Profile
    start: Cell | None
    end: Cell | None


class _SectionBuilder:
    def __init__(self, grid: _SearchGrid, targets: Sequence[Target], routes: _Routes,
                 params: RoadNetworkParams) -> None:
        self._g = grid
        self._targets = targets
        self._routes = routes
        self._p = params
        self._aligner = AlignmentBuilder(params.min_radius_game_m, params.straighten_game_m, params.sample_game_m)
        self._profiles = ProfilePlanner(ProfileRules(
            params.max_grade, params.profile_smooth_game_m, params.approach_smooth_game_m, params.bridge_from_game_m,
            params.tunnel_from_game_m, params.min_bridge_game_m, params.min_tunnel_game_m,
            params.max_span_extend_game_m, params.max_structure_gap_game_m, params.wet_clearance_game_m,
            params.structure_max_grade))

    def sections(self) -> list[_Section]:
        adj: dict[Cell, set[Cell]] = {}
        for a, b in self._routes.edges:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        stops = {c for c, n in adj.items() if len(n) != 2} | set(self._routes.target_cells)
        out = []
        for chain in _chains(adj, stops):
            section = self._section(chain)
            if section is not None:
                out.append(section)
        return out

    def _section(self, chain: Sequence[Cell]) -> _Section | None:
        g = self._g
        legs = [Leg(g.centre(a), g.centre(b), self._routes.edges[(min(a, b), max(a, b))])
                for a, b in itertools.pairwise(chain)]
        alignment = self._aligner.build(legs)
        start, end = chain[0], chain[-1]
        alignment, start_key = self._trim(alignment, start, at_start=True)
        alignment, end_key = self._trim(alignment, end, at_start=False)
        if len(alignment.points) < 2 or alignment.length_m < self._p.sample_game_m:
            return None
        x, y = alignment.points[:, 0], alignment.points[:, 1]
        ground = g.ground(x, y)
        steps = np.hypot(*np.diff(alignment.points, axis=0).T)
        zone, core = g.wet_at(x, y)
        profile = self._profiles.plan(ground, steps, alignment.structure, zone, core)
        return _Section(alignment, self._meet_ground(profile, ground, steps), start_key, end_key)

    def _trim(self, alignment: Alignment, cell: Cell, at_start: bool) -> tuple[Alignment, Cell | None]:
        """Cuts the line where it enters the town or industry area around a target end; the end of a junction stays
        (its cell is the key of the shared node)."""
        k = self._routes.target_cells.get(cell)
        if k is None:
            return alignment, cell
        t = self._targets[k]
        pts, flags = alignment.points, alignment.structure
        if at_start:
            pts, flags = pts[::-1], flags[::-1]
        outside = np.hypot(pts[:, 0] - t.x, pts[:, 1] - t.y) >= t.radius
        last = int(np.flatnonzero(outside)[-1]) if outside.any() else -1
        pts, flags = pts[: last + 1], flags[: max(0, last)]
        if at_start:
            pts, flags = pts[::-1], flags[::-1]
        return Alignment(pts, flags), None

    def _meet_ground(self, profile: Profile, ground: FloatMap, steps: FloatMap) -> Profile:
        """Both ends at the ground height (the same at every section of a junction), blended in over end_blend."""
        height = profile.height.copy()
        s = np.concatenate([[0], np.cumsum(steps)])
        blend = max(self._p.end_blend_game_m, 1e-6)
        for at, dist in ((0, s), (-1, s[-1] - s)):
            height += (ground[at] - profile.height[at]) * np.clip(1 - dist / blend, 0, 1)
        return Profile(height, profile.kinds)


def _chains(adj: dict[Cell, set[Cell]], stops: set[Cell]) -> list[list[Cell]]:
    """The cell graph split into chains between stop cells (and pure rings)."""
    chains, done = [], set()
    for s in sorted(c for c in adj if c in stops):
        for nb in sorted(adj[s]):
            if (s, nb) in done:
                continue
            chain = [s, nb]
            done.update({(s, nb), (nb, s)})
            while chain[-1] not in stops:
                nxt = next(c for c in sorted(adj[chain[-1]]) if c != chain[-2])
                done.update({(chain[-1], nxt), (nxt, chain[-1])})
                chain.append(nxt)
            chains.append(chain)
    for s in sorted(adj):                                  # pure rings without a stop
        for nb in sorted(adj[s]):
            if (s, nb) not in done:
                chain = [s, nb]
                done.update({(s, nb), (nb, s)})
                while chain[-1] != s:
                    nxt = next(c for c in sorted(adj[chain[-1]]) if (chain[-1], c) not in done)
                    done.update({(chain[-1], nxt), (nxt, chain[-1])})
                    chain.append(nxt)
                chains.append(chain)
    return chains


# --- plan ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Samples:
    """Dense samples of the normal ways for the terrain: positions (game m), road heights (game m)."""

    x: FloatMap
    y: FloatMap
    height: FloatMap


class _PlanWriter:
    """Sections -> ways split at kind changes, nodes every node_game_m; junction nodes shared."""

    def __init__(self, grid: _SearchGrid, targets: Sequence[Target], params: RoadNetworkParams) -> None:
        self._g = grid
        self._targets = targets
        self._p = params

    def plan(self, sections: Sequence[_Section]) -> tuple[RoadPlan, _Samples]:
        nodes: list[list[float]] = []
        shared: dict[Cell, int] = {}
        ways: list[dict[str, Any]] = []
        normal: list[tuple[FloatMap, FloatMap, FloatMap]] = []

        def node(i: int, s: _Section, key: Cell | None = None) -> int:
            if key is not None and key in shared:
                return shared[key]
            x, y = s.alignment.points[i]
            nodes.append([round(x / self._g.width_m, 7), round(y / self._g.height_m, 7),
                          round(float(s.profile.height[i]), 2)])
            if key is not None:
                shared[key] = len(nodes) - 1
            return len(nodes) - 1

        for s in sections:
            kinds = s.profile.kinds
            last = len(s.alignment.points) - 1
            breaks = [0, *(np.flatnonzero(np.diff(kinds) != 0) + 1).tolist(), last]
            first_node = node(0, s, s.start)
            for a, b in itertools.pairwise(breaks):
                ids = [first_node]
                for i in self._thinned(s, a, b):
                    ids.append(node(i, s))
                first_node = node(b, s, s.end if b == last else None)
                ids.append(first_node)
                ways.append({"kind": KIND_NAMES[int(kinds[a])], "nodes": ids})
                if kinds[a] == NORMAL:
                    pts = s.alignment.points[a: b + 1]
                    normal.append((pts[:, 0], pts[:, 1], s.profile.height[a: b + 1]))
        targets = [{"name": t.name, "kind": t.kind, "size": t.size, "u": round(t.x / self._g.width_m, 7),
                    "v": round(t.y / self._g.height_m, 7)} for t in self._targets]
        samples = _Samples(*(np.concatenate([n[k] for n in normal]) if normal else np.zeros(0) for k in range(3)))
        return RoadPlan({"targets": targets, "nodes": nodes, "ways": ways}), samples

    def _thinned(self, s: _Section, a: int, b: int) -> list[int]:
        """Inner sample indices of a..b about every node_game_m."""
        pts = s.alignment.points
        steps = np.hypot(*np.diff(pts[a: b + 1], axis=0).T)
        dist = np.concatenate([[0], np.cumsum(steps)])
        total = dist[-1]
        n = max(1, round(total / self._p.node_game_m))
        wanted = [total * k / n for k in range(1, n)]
        return sorted({a + int(np.argmin(np.abs(dist - w))) for w in wanted} - {a, b})


# --- terrain -------------------------------------------------------------------------------------------------------


class _TerrainShaper:
    """The terrain under the normal ways at the road height: level across, banks to the side; water stays."""

    def __init__(self, grid: _SearchGrid, params: RoadNetworkParams) -> None:
        self._g = grid
        self._p = params

    def shape(self, heights: Field, keep: NDArray[np.bool_], samples: _Samples) -> tuple[Field, Mask]:
        """`keep`: water and riverbeds stay as they are."""
        dem = heights.values
        if len(samples.x) == 0:
            return heights, Mask(np.zeros(dem.shape, dtype=bool), heights.frame)
        g, p = self._g, self._p
        rows, cols = dem.shape
        px = samples.x / g.width_m * (cols - 1)
        py = samples.y / g.height_m * (rows - 1)
        target = samples.height / g.height_game_per_real + g.land_min_m            # real metres
        ground = map_coordinates(dem, [py, px], order=1, mode="nearest")
        px_real_m = GAME_METERS_PER_PIXEL / g.height_game_per_real
        half = np.full(len(px), p.width_game_m / GAME_METERS_PER_PIXEL / 2)
        blend = np.maximum(p.blend_min_px, np.abs(target - ground) / p.bank_slope / px_real_m)
        weighted, wmax = LineStamp(dem).stamp(px.astype(np.float32), py.astype(np.float32),
                                              target.astype(np.float32), half.astype(np.float32),
                                              blend.astype(np.float32), cosine=True)
        wmax[keep] = 0
        change = wmax * (weighted - dem)
        return heights.like(dem + change), Mask(wmax >= FULL_WEIGHT, heights.frame)


# --- preview -------------------------------------------------------------------------------------------------------


def _preview(base: Image, grid: _SearchGrid, targets: Sequence[Target], sections: Sequence[_Section],
             routes: _Routes, width: int) -> PilImage.Image:
    """Normal ways red, bridges blue, tunnels black on the wet zone (light blue); links without a road yellow; towns
    white, industries grey."""
    img = PilImage.fromarray(base.pixels).convert("RGB")
    img = img.resize((width, round(img.size[1] * width / img.size[0])))
    zone = PilImage.fromarray(grid.fine_wet.zone.astype(np.uint8) * 255).resize(img.size)
    img.paste(PilImage.new("RGB", img.size, (150, 200, 255)), mask=zone)
    pw, ph = img.size
    sx, sy = pw / grid.width_m, ph / grid.height_m
    draw = ImageDraw.Draw(img)
    for i, j, _ in routes.failed:
        if j >= 0:
            a, b = targets[i], targets[j]
            draw.line([(a.x * sx, a.y * sy), (b.x * sx, b.y * sy)], fill=(255, 220, 0), width=1)
    colours = {NORMAL: (220, 0, 0), BRIDGE: (0, 90, 255), TUNNEL: (0, 0, 0)}
    for s in sections:
        pts = s.alignment.points
        for k in range(len(pts) - 1):
            draw.line([(pts[k][0] * sx, pts[k][1] * sy), (pts[k + 1][0] * sx, pts[k + 1][1] * sy)],
                      fill=colours[int(s.profile.kinds[k])], width=3)
    font = preview_font(14)
    for t in targets:
        x, y = t.x * sx, t.y * sy
        if t.kind == "town":
            r = 3 + 2.5 * math.sqrt(max(t.size, 1) / 100)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255), outline=(0, 0, 0), width=2)
            draw.text((x + r + 3, y - 8), t.name, fill=(0, 0, 0), font=font, stroke_width=2,
                      stroke_fill=(255, 255, 255))
        else:
            draw.rectangle([x - 3, y - 3, x + 3, y + 3], fill=(150, 150, 150), outline=(0, 0, 0))
    return img
