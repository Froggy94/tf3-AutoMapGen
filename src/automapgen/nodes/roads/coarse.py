"""Coarse route search over the whole map: cells with mean values, no headings.

Built once per map and used for every link: 16 directions, too steep moves allowed as serpentines (`MoveTable`),
straight structure moves over water and gorges. Cells of planned roads become cheaper (bundling). The coarse route
is the corridor the fine search (`lattice`) works in.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import distance_transform_edt
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from automapgen.nodes.roads.lattice import DIRECTIONS, LatticeCosts, MoveTable, Terrain

type Cell = tuple[int, int]


class CoarseGraph:
    def __init__(self, terrain: Terrain, costs: LatticeCosts, serpentine: float, reuse_factor: float) -> None:
        self._rows, self._cols = terrain.shape
        self._reuse_factor = reuse_factor
        moves = MoveTable(terrain, costs, serpentine)
        parts = [moves.normal(dx, dy) for dx, dy in DIRECTIONS]
        parts += [moves.structure(dx, dy, steps) for dx, dy in DIRECTIONS for steps in costs.structure_steps]
        self._src = np.concatenate([p[0] for p in parts])
        self._dst = np.concatenate([p[1] for p in parts])
        self._cost = np.concatenate([p[2] for p in parts])
        self._used = np.zeros(self._rows * self._cols, dtype=bool)
        self._matrix: csr_matrix | None = None

    def mark_used(self, cells: Iterable[Cell]) -> None:
        """Planned road cells: moves between two of them cost only reuse_factor next time."""
        for r, c in cells:
            self._used[r * self._cols + c] = True
        self._matrix = None

    def route(self, start: Cell, goals: Sequence[Cell]) -> list[Cell] | None:
        """Cells of the cheapest route from `start` to the nearest goal, None if unreachable."""
        source = self._index(start)
        dist, pred = dijkstra(self._graph(), indices=source, return_predecessors=True)
        best = min((self._index(g) for g in goals), key=lambda i: dist[i])
        if not np.isfinite(dist[best]):
            return None
        path = [best]
        while path[-1] != source:
            path.append(int(pred[path[-1]]))
        return [divmod(i, self._cols) for i in reversed(path)]

    def _graph(self) -> csr_matrix:
        if self._matrix is None:
            reused = self._used[self._src] & self._used[self._dst]
            data = np.where(reused, self._cost * self._reuse_factor, self._cost)
            size = self._rows * self._cols
            self._matrix = csr_matrix((data, (self._src, self._dst)), shape=(size, size))
        return self._matrix

    def _index(self, cell: Cell) -> int:
        return cell[0] * self._cols + cell[1]


def corridor(path: Sequence[tuple[float, float]], shape: tuple[int, int], origin: tuple[float, float],
             cell_m: float, width_m: float) -> NDArray[np.bool_]:
    """Cells of a window (origin = its top-left corner in game metres) within `width_m` of the polyline."""
    rows, cols = shape
    on_line = np.zeros(shape, dtype=bool)
    pts = np.array(path)
    for a, b in itertools.pairwise(pts):
        n = max(2, int(np.ceil(np.hypot(*(b - a)) / (cell_m / 2))) + 1)
        line = a + (b - a) * np.linspace(0, 1, n)[:, None]
        r = np.clip(((line[:, 1] - origin[1]) / cell_m).astype(int), 0, rows - 1)
        c = np.clip(((line[:, 0] - origin[0]) / cell_m).astype(int), 0, cols - 1)
        on_line[r, c] = True
    if len(pts) == 1:
        on_line[min(rows - 1, int((pts[0][1] - origin[1]) / cell_m)), min(cols - 1, int((pts[0][0] - origin[0])
                                                                                       / cell_m))] = True
    result: NDArray[np.bool_] = distance_transform_edt(~on_line) * cell_m <= width_m
    return result
