"""Route search on a heading lattice: grid cells x 16 headings.

A normal move goes to a neighbour in one of 16 directions and may turn against the previous move only as much as the
minimum radius allows (an arc of that radius must fit between the middles of both moves). A structure move
(bridge or tunnel) continues straight over several cells at a constant grade. Costs: length x land cover factor x
grade term, a small surcharge per turn; structures cost per metre above (bridge) or below (tunnel) the ground.
Lengths and heights in game metres; cells are (row, column) of a window of the map grid.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

DIRECTIONS: tuple[tuple[int, int], ...] = (
    (1, 0), (2, 1), (1, 1), (1, 2), (0, 1), (-1, 2), (-1, 1), (-2, 1),
    (-1, 0), (-2, -1), (-1, -1), (-1, -2), (0, -1), (1, -2), (1, -1), (2, -1))   # (dx, dy); index = heading
HEADINGS = len(DIRECTIONS)

type FloatMap = NDArray[np.float64]
type BoolMap = NDArray[np.bool_]


@dataclass(frozen=True)
class LatticeCosts:
    """Cost settings of the search (game metres, grades as height / distance)."""

    cell_m: float
    min_radius_m: float
    max_grade: float
    slope_weight: float
    turn_weight: float
    structure_steps: tuple[int, ...]       # lengths of structure moves in moves of one direction step
    max_structure_m: float
    bridge_cost: float                     # surcharge per metre of bridge (cost of a normal metre = 1)
    tunnel_cost: float
    structure_fixed_m: float               # fixed cost of a structure (abutments, portals), in metres of road


@dataclass(frozen=True)
class Terrain:
    """The window: ground height (game m), land cover factor (>= 1), cells no road may touch (`blocked`) and cells
    only structures may cross (`crossing_only`, e.g. water)."""

    height: FloatMap
    factor: FloatMap
    blocked: BoolMap
    crossing_only: BoolMap

    @property
    def shape(self) -> tuple[int, int]:
        rows, cols = self.height.shape
        return rows, cols


@dataclass(frozen=True)
class Step:
    """One move of a route: from cell to cell; `structure` for bridge/tunnel moves."""

    start: tuple[int, int]
    end: tuple[int, int]
    structure: bool


class TurnRule:
    """Which heading may follow which: the turn between two moves must leave room for an arc of the minimum radius
    between their middles; the angle of each allowed turn."""

    def __init__(self, cell_m: float, min_radius_m: float) -> None:
        self.allowed: list[list[tuple[int, float]]] = []
        for ax, ay in DIRECTIONS:
            row = []
            for h_out, (bx, by) in enumerate(DIRECTIONS):
                angle = abs(math.atan2(ax * by - ay * bx, ax * bx + ay * by))
                shorter = min(math.hypot(ax, ay), math.hypot(bx, by)) * cell_m
                if angle < 1e-9 or (angle < math.pi / 2 and shorter / 2 / math.tan(angle / 2) >= min_radius_m):
                    row.append((h_out, angle))
            self.allowed.append(row)


class LatticeGraph:
    """The state graph of one window: state = heading x cell; built with numpy, searched with scipy's Dijkstra."""

    def __init__(self, terrain: Terrain, costs: LatticeCosts) -> None:
        self._t = terrain
        self._c = costs
        self._rows, self._cols = terrain.shape
        self._cells = self._rows * self._cols
        self.matrix = self._build()

    def state(self, heading: int, cell: tuple[int, int]) -> int:
        return heading * self._cells + cell[0] * self._cols + cell[1]

    def cell(self, state: int) -> tuple[int, int]:
        row, col = divmod(state % self._cells, self._cols)
        return row, col

    def route(self, start: tuple[int, int], goals: list[tuple[int, int]]) -> tuple[list[Step], float] | None:
        """Cheapest route from `start` (any heading) to the nearest of `goals` (any heading), None if unreachable."""
        sources = [self.state(h, start) for h in range(HEADINGS)]
        dist, pred, _ = dijkstra(self.matrix, indices=sources, return_predecessors=True, min_only=True)
        targets = [self.state(h, g) for g in goals for h in range(HEADINGS)]
        best = min(targets, key=lambda s: dist[s])
        if not np.isfinite(dist[best]):
            return None
        states = [best]
        while states[-1] not in sources:
            states.append(int(pred[states[-1]]))
        states.reverse()
        steps = []
        for a, b in itertools.pairwise(states):
            ca, cb = self.cell(a), self.cell(b)
            hx, hy = DIRECTIONS[b // self._cells]
            steps.append(Step(ca, cb, abs(cb[1] - ca[1]) > abs(hx) or abs(cb[0] - ca[0]) > abs(hy)))
        return steps, float(dist[best])

    def _build(self) -> csr_matrix:
        rows: list[NDArray[np.int64]] = []
        cols: list[NDArray[np.int64]] = []
        data: list[FloatMap] = []
        moves = MoveTable(self._t, self._c)
        turns = TurnRule(self._c.cell_m, self._c.min_radius_m)
        for h_out, (dx, dy) in enumerate(DIRECTIONS):
            src, dst, cost = moves.normal(dx, dy)
            for h_in in range(HEADINGS):
                for allowed, angle in turns.allowed[h_in]:
                    if allowed == h_out:
                        rows.append(src + h_in * self._cells)
                        cols.append(dst + h_out * self._cells)
                        data.append(cost + self._c.turn_weight * angle * self._c.cell_m)
            for steps in self._c.structure_steps:
                src, dst, cost = moves.structure(dx, dy, steps)
                rows.append(src + h_out * self._cells)
                cols.append(dst + h_out * self._cells)
                data.append(cost)
        size = HEADINGS * self._cells
        return csr_matrix((np.concatenate(data), (np.concatenate(rows), np.concatenate(cols))), shape=(size, size))


type Moves = tuple[NDArray[np.int64], NDArray[np.int64], FloatMap]


class MoveTable:
    """All moves by one direction step (or a structure over several) inside a window: start and end cell indices
    (row-major) and costs. `serpentine` > 1 also allows moves up to that many times the maximum grade, at a cost
    growing with the grade like the longer line of serpentines (for coarse grids, whose mean heights hide the
    slopes a road can wind up)."""

    def __init__(self, terrain: Terrain, costs: LatticeCosts, serpentine: float = 1.0) -> None:
        self._t = terrain
        self._c = costs
        self._serpentine = serpentine
        self._rows, self._cols = terrain.shape
        self._free = ~(terrain.blocked | terrain.crossing_only)
        self._index = np.arange(self._rows * self._cols, dtype=np.int64).reshape(self._rows, self._cols)

    def normal(self, dx: int, dy: int) -> Moves:
        t, c = self._t, self._c
        ys, xs, yt, xt = self._pairs(dx, dy)
        length = math.hypot(dx, dy) * c.cell_m
        relative = np.abs(t.height[yt, xt] - t.height[ys, xs]) / length / c.max_grade
        ok = self._free[ys, xs] & self._free[yt, xt] & (relative <= self._serpentine)
        grade_term = np.where(relative <= 1, 1 + c.slope_weight * relative ** 2, (1 + c.slope_weight) * relative)
        cost = length * (t.factor[ys, xs] + t.factor[yt, xt]) / 2 * grade_term
        return self._index[ys, xs][ok], self._index[yt, xt][ok], cost[ok]

    def structure(self, dx: int, dy: int, steps: int) -> Moves:
        """Straight moves over `steps` direction steps at a constant grade; cost by the height above or below the
        ground at every step in between."""
        t, c = self._t, self._c
        length = math.hypot(dx, dy) * c.cell_m * steps
        if length > c.max_structure_m:
            empty = np.zeros(0, dtype=np.int64)
            return empty, empty, np.zeros(0)
        ys, xs, yt, xt = self._pairs(dx * steps, dy * steps)
        start, end = t.height[ys, xs], t.height[yt, xt]
        ok = self._free[ys, xs] & self._free[yt, xt] & (np.abs(end - start) / length <= c.max_grade)
        cost = np.full(start.shape, c.structure_fixed_m + length / steps)       # fixed part and the last step
        for k in range(1, steps):
            between = (slice(ys.start + dy * k, ys.stop + dy * k), slice(xs.start + dx * k, xs.stop + dx * k))
            above = start + (end - start) * k / steps > t.height[between]
            cost += length / steps * np.where(above, 1 + c.bridge_cost, 1 + c.tunnel_cost)
            ok &= ~t.blocked[between]                             # water may be crossed, blocked cells not
        return self._index[ys, xs][ok], self._index[yt, xt][ok], cost[ok]

    def _pairs(self, dx: int, dy: int) -> tuple[slice, slice, slice, slice]:
        """Slices of the start and end cells of all moves by (dx, dy) inside the window."""
        ys = slice(max(0, -dy), self._rows - max(0, dy))
        xs = slice(max(0, -dx), self._cols - max(0, dx))
        yt = slice(max(0, dy), self._rows - max(0, -dy))
        xt = slice(max(0, dx), self._cols - max(0, -dx))
        return ys, xs, yt, xt
