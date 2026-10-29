"""Painted rivers moved into the valley line: OSM and the elevation model do not always agree (OSM a few metres off,
narrow gorges blurred in a 30 m model), so a painted riverbed can lie on the slope beside the valley. Real water is
not moved (its band and level come from the WorldCover water area and the water line).

Each run of painted segments of the plan is resampled along its course and moved sideways within a corridor beside
the OSM course. The move is chosen for the whole run at once (dynamic programming): as low as possible, a move costs
height (OSM stays where the valley is flat), the move changes only slowly along the course, and it is 0 at both ends
(junctions and the transition to real water stay where they are).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, NamedTuple

import numpy as np
from numpy.typing import NDArray
from PIL import Image as PilImage
from PIL import ImageDraw
from scipy.ndimage import map_coordinates, uniform_filter1d

from automapgen.graph import NodeContext, NodeParams, node, param
from automapgen.nodes.images import Image
from automapgen.nodes.maps import GAME_METERS_PER_PIXEL
from automapgen.nodes.rasters import Field
from automapgen.nodes.rivers import RiverPlan, hill_shade, preview_font

_STEP_PX = 2.0
"""Distance of the resampled points along the course (heightmap pixels)."""
_ATTRIBUTES = ("width_game_m", "level_m", "floor_m")
"""Values per point, interpolated along the resampled course."""
_DECIMALS = {"width_game_m": 1, "level_m": 1, "floor_m": 1}
_OSM_COLOR = (90, 90, 90)
_MOVE_COLORS = ((40, 170, 60), (240, 200, 0), (220, 20, 20))   # no move, half the corridor, the whole corridor


class ValleyParams(NodeParams):
    max_move_real_m: float = param(100.0, ge=0, unit="m")
    """The painted riverbed may move at most this far beside the OSM course (real metres; 0 = keep OSM)."""
    lower_per_100m_m: float = param(3.0, ge=0, unit="m")
    """A move pays only where the bed gets lower by more than this per 100 m of move (keeps OSM where the valley is
    flat)."""
    max_bend: float = param(0.5, gt=0, le=1)
    """Sideways change of the move per metre along the course at most (smooth course)."""
    smooth_real_m: float = param(60.0, ge=0, unit="m")
    """The move is averaged over this distance along the course."""
    preview_width: int = param(2000, ge=100)
    """Width of the preview picture in pixels."""


class RiverValley(NamedTuple):
    plan: RiverPlan
    preview: Image


@node("rivers.to_valley")
def to_valley(plan: RiverPlan, heights: Field, params: ValleyParams, context: NodeContext) -> RiverValley:
    """The river plan with the painted rivers moved into the valley line of `heights` (original elevation on the
    heightmap grid); the preview shows the moved courses coloured by the size of the move."""
    frame = heights.frame
    real_per_px = GAME_METERS_PER_PIXEL / frame.horizontal_scale
    corridor = _Corridor(heights.values, params, real_per_px)
    mover = _PlanMover(corridor, heights.values.shape)
    moved = mover.move(plan) if corridor.reach_px > 0 else plan
    mover.statistics.log(context.log, real_per_px, params.max_move_real_m)
    preview = _MovePreview(params.preview_width).draw(heights.values, mover.runs, corridor.reach_px)
    return RiverValley(moved, Image(np.asarray(preview)))


@dataclass
class _Run:
    """A resampled painted run: points before and after the move (heightmap pixels)."""

    before: NDArray[np.float64]
    after: NDArray[np.float64]
    move_px: NDArray[np.float64]
    lowered_m: NDArray[np.float64]


@dataclass
class _Statistics:
    samples: int = 0
    moved: int = 0
    lowered_m: float = 0.0
    largest_px: float = 0.0
    moves_px: list[float] = field(default_factory=list)

    def add(self, run: _Run) -> None:
        moved = np.abs(run.move_px) >= 1
        self.samples += len(run.move_px)
        self.moved += int(moved.sum())
        self.lowered_m += float(run.lowered_m[moved].sum())
        self.largest_px = max(self.largest_px, float(np.abs(run.move_px).max(initial=0)))
        self.moves_px += np.abs(run.move_px[moved]).tolist()

    def log(self, log: Callable[[str], None], real_per_px: float, max_move_real_m: float) -> None:
        if max_move_real_m <= 0 or self.samples == 0:
            log("painted rivers stay on the OSM course" + (" (no painted rivers)" if max_move_real_m > 0 else ""))
            return
        km = self.samples * _STEP_PX * real_per_px / 1000
        moved_km = self.moved * _STEP_PX * real_per_px / 1000
        median = float(np.median(self.moves_px)) * real_per_px if self.moves_px else 0.0
        lowered = self.lowered_m / self.moved if self.moved else 0.0
        log(f"painted rivers {km:.0f} km real, moved into the valley line on {moved_km:.1f} km "
            f"({moved_km / km * 100:.0f} %): median {median:.0f} m, at most {self.largest_px * real_per_px:.0f} m "
            f"(corridor {max_move_real_m:g} m), there {lowered:.1f} m lower on average")


class _Corridor:
    """Finds the move of one resampled run within the corridor (dynamic programming over all its points)."""

    def __init__(self, heights: NDArray[np.float32], params: ValleyParams, real_per_px: float) -> None:
        self._heights = heights
        self.reach_px = round(params.max_move_real_m / real_per_px)
        self._offsets = np.arange(-self.reach_px, self.reach_px + 1, dtype=np.float64)
        self._cost_per_px = params.lower_per_100m_m / 100 * real_per_px
        self._max_step = max(1, int(params.max_bend * _STEP_PX))
        self._smooth = max(1, round(params.smooth_real_m / real_per_px / _STEP_PX))

    def move(self, points: NDArray[np.float64]) -> _Run:
        """`points` resampled every _STEP_PX; the first and the last stay."""
        normals = self._normals(points)
        xs = points[:, None, 0] + normals[:, None, 0] * self._offsets
        ys = points[:, None, 1] + normals[:, None, 1] * self._offsets
        heights = map_coordinates(self._heights, [ys, xs], order=1, mode="nearest").astype(np.float64)
        cost = heights + self._cost_per_px * np.abs(self._offsets)
        centre = self.reach_px
        cost[[0, -1]] = np.inf
        cost[[0, -1], centre] = heights[[0, -1], centre]
        chosen = self._cheapest_path(cost)
        move = uniform_filter1d(self._offsets[chosen], self._smooth, mode="constant", cval=0.0)
        move[[0, -1]] = 0.0
        after = points + normals * move[:, None]
        lowered = heights[:, centre] - map_coordinates(self._heights, [after[:, 1], after[:, 0]], order=1,
                                                       mode="nearest")
        return _Run(points, after, move, lowered)

    def _normals(self, points: NDArray[np.float64]) -> NDArray[np.float64]:
        """Left normals of the course, from the direction averaged over the corridor width (fewer crossings in
        bends)."""
        direction = uniform_filter1d(np.gradient(points, axis=0), max(3, 2 * self.reach_px // int(_STEP_PX) + 1),
                                     axis=0, mode="nearest")
        direction /= np.maximum(np.linalg.norm(direction, axis=1, keepdims=True), 1e-9)
        return np.stack([-direction[:, 1], direction[:, 0]], axis=1)

    def _cheapest_path(self, cost: NDArray[np.float64]) -> NDArray[np.intp]:
        """Index of the offset per point: least total cost, the offset changing by at most _max_step per point."""
        n, k = cost.shape
        steps = range(-self._max_step, self._max_step + 1)
        total = cost[0].copy()
        came_from = np.zeros((n, k), dtype=np.intp)
        index = np.arange(k)
        for i in range(1, n):
            best = np.full(k, np.inf)
            origin = index.copy()
            for s in steps:
                shifted = np.full(k, np.inf)
                if s >= 0:
                    shifted[s:] = total[:k - s]
                else:
                    shifted[:s] = total[-s:]
                better = shifted < best
                best[better] = shifted[better]
                origin[better] = index[better] - s
            total = cost[i] + best
            came_from[i] = origin
        path = np.empty(n, dtype=np.intp)
        path[-1] = int(np.argmin(total))
        for i in range(n - 1, 0, -1):
            path[i - 1] = came_from[i, path[i]]
        return path


class _PlanMover:
    """Moves the painted runs of every river section; real stretches keep their points."""

    def __init__(self, corridor: _Corridor, shape: tuple[int, ...]) -> None:
        self._corridor = corridor
        self._scale = np.array([shape[1] - 1, shape[0] - 1], dtype=np.float64)
        self.runs: list[_Run] = []
        self.statistics = _Statistics()

    def move(self, plan: RiverPlan) -> RiverPlan:
        return RiverPlan({**plan.data, "rivers": [self._river(r) for r in plan.data["rivers"]]})

    def _river(self, river: dict[str, Any]) -> dict[str, Any]:
        real = river["real"]
        count = len(real)
        painted = [not (real[k] and real[k + 1]) for k in range(count - 1)]
        out: dict[str, list[Any]] = {"points": [], "real": [], "navigable": [], **{a: [] for a in _ATTRIBUTES}}
        k = 0
        while k < count - 1:
            if not painted[k]:
                self._append_vertex(out, river, k)
                k += 1
                continue
            end = k
            while end < count - 1 and painted[end]:
                end += 1
            self._append_run(out, river, k, end)
            k = end
        self._append_vertex(out, river, count - 1)
        return {**river, **out}

    @staticmethod
    def _append_vertex(out: dict[str, list[Any]], river: dict[str, Any], k: int) -> None:
        out["points"].append(river["points"][k])
        out["real"].append(river["real"][k])
        out["navigable"].append(river.get("navigable", [False] * len(river["points"]))[k])
        for a in _ATTRIBUTES:
            out[a].append(river[a][k])

    def _append_run(self, out: dict[str, list[Any]], river: dict[str, Any], start: int, end: int) -> None:
        """Vertices start..end resampled and moved, without the last one (it follows as the next vertex)."""
        vertices = np.array(river["points"][start:end + 1], dtype=np.float64) * self._scale
        along = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(vertices, axis=0), axis=1))])
        if along[-1] < 3 * _STEP_PX:
            for k in range(start, end):
                self._append_vertex(out, river, k)
            return
        at = np.linspace(0.0, along[-1], int(np.ceil(along[-1] / _STEP_PX)) + 1)
        points = np.stack([np.interp(at, along, vertices[:, 0]), np.interp(at, along, vertices[:, 1])], axis=1)
        run = self._corridor.move(points)
        self.runs.append(run)
        self.statistics.add(run)
        fractions = run.after / self._scale
        out["points"] += [[round(float(u), 6), round(float(v), 6)] for u, v in fractions[:-1]]
        out["real"] += [river["real"][start]] + [False] * (len(at) - 2)
        navigable = river.get("navigable", [False] * len(river["points"]))
        out["navigable"] += [navigable[start]] + [all(navigable[start:end + 1])] * (len(at) - 2)
        for a in _ATTRIBUTES:
            values = np.interp(at, along, np.array(river[a][start:end + 1], dtype=np.float64))
            out[a] += [round(float(x), _DECIMALS[a]) for x in values[:-1]]


class _MovePreview:
    """Hill shading, the OSM course grey, the moved course coloured by the size of the move."""

    def __init__(self, preview_width: int) -> None:
        self._pw = preview_width

    def draw(self, dem: NDArray[np.float32], runs: list[_Run], reach_px: int) -> PilImage.Image:
        height, width = dem.shape
        pw = self._pw
        ph = round(pw * height / width)
        img = PilImage.fromarray(hill_shade(dem, pw, ph))
        d = ImageDraw.Draw(img)
        f = pw / (width - 1)
        for run in runs:
            d.line([(x * f, y * f) for x, y in run.before], fill=_OSM_COLOR, width=1)
        for run in runs:
            share = np.abs(run.move_px) / max(reach_px, 1)
            for k in range(len(run.after) - 1):
                (x0, y0), (x1, y1) = run.after[k], run.after[k + 1]
                d.line([(x0 * f, y0 * f), (x1 * f, y1 * f)], fill=_ramp(float(share[k])), width=2)
        font = preview_font(16, bold=True)
        for k, (col, txt) in enumerate([(_OSM_COLOR, "painted river on the OSM course"),
                                        (_MOVE_COLORS[0], "moved course: not moved"),
                                        (_MOVE_COLORS[1], "moved half the corridor"),
                                        (_MOVE_COLORS[2], "moved the whole corridor")]):
            d.rectangle((10, 10 + 22 * k, 28, 24 + 22 * k), fill=col, outline=(0, 0, 0))
            d.text((34, 9 + 22 * k), txt, fill=(0, 0, 0), font=font, stroke_width=2, stroke_fill=(255, 255, 255))
        return img


def _ramp(share: float) -> tuple[int, int, int]:
    """Green (no move) -> yellow (half) -> red (the whole corridor)."""
    low, high, t = (_MOVE_COLORS[0], _MOVE_COLORS[1], share * 2) if share < 0.5 else \
        (_MOVE_COLORS[1], _MOVE_COLORS[2], min(1.0, share * 2 - 1))
    r, g, b = (round(a + (c - a) * t) for a, c in zip(low, high, strict=True))
    return r, g, b
