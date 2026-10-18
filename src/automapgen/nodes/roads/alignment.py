"""Horizontal alignment of a lattice route: straights and arcs.

The cell centres of the route become a polyline; straight runs that zigzag between neighbouring headings are
straightened (as long as every corner still leaves room for the minimum radius), then every corner is rounded by
the largest arc that fits between the middles of its two legs. Structure moves (bridges, tunnels) stay straight
legs; an arc belongs to a structure only between two structure legs. Lengths in game metres.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

type Points = NDArray[np.float64]


@dataclass(frozen=True)
class Leg:
    """A straight piece between two corners; `structure` for bridge/tunnel legs."""

    start: tuple[float, float]
    end: tuple[float, float]
    structure: bool


@dataclass(frozen=True)
class Alignment:
    """The sampled road line (game metres) and, per segment between two samples, whether it lies on a structure leg."""

    points: Points
    structure: NDArray[np.bool_]

    @property
    def length_m(self) -> float:
        return float(np.hypot(*np.diff(self.points, axis=0).T).sum())


class AlignmentBuilder:
    """Legs -> straightened corners -> arcs -> samples every `step_m`."""

    def __init__(self, min_radius_m: float, straighten_m: float, step_m: float) -> None:
        self._min_radius = min_radius_m
        self._tolerance = straighten_m
        self._step = step_m

    def build(self, legs: Sequence[Leg]) -> Alignment:
        corners, structure = self._corners(legs)
        return self._sample(corners, structure)

    def _corners(self, legs: Sequence[Leg]) -> tuple[Points, list[bool]]:
        """Corner points and per leg the structure flag; normal runs straightened where the radius allows."""
        points: list[tuple[float, float]] = [legs[0].start]
        flags: list[bool] = []
        run: list[tuple[float, float]] = [legs[0].start]
        for leg in legs:
            if leg.structure:
                self._close_run(run, points, flags)
                points.append(leg.end)
                flags.append(True)
                run = [leg.end]
            else:
                run.append(leg.end)
        self._close_run(run, points, flags)
        return np.array(points), flags

    def _close_run(self, run: list[tuple[float, float]], points: list[tuple[float, float]], flags: list[bool]) -> None:
        if len(run) < 2:
            return
        merged = _without_straight_points(np.array(run))
        straight = _douglas_peucker(merged, self._tolerance)
        kept = straight if self._radius_fits(straight) else merged
        points.extend(map(tuple, kept[1:]))
        flags.extend([False] * (len(kept) - 1))

    def _radius_fits(self, p: Points) -> bool:
        return all(_corner_radius(p[i - 1], p[i], p[i + 1]) >= self._min_radius - 1e-6 for i in range(1, len(p) - 1))

    def _sample(self, corners: Points, structure: list[bool]) -> Alignment:
        """Arcs at the corners (tangent length = half the shorter leg), samples every step."""
        out: list[Points] = [corners[:1]]
        flags: list[bool] = []
        n = len(corners)
        entry = corners[0]
        for i in range(1, n):
            a, b = corners[i - 1], corners[i]
            if i == n - 1:
                straight = _densify(entry, b, self._step)
                out.append(straight[1:])
                flags += [structure[i - 1]] * (len(straight) - 1)
                break
            c = corners[i + 1]
            tangent = min(np.hypot(*(b - a)), np.hypot(*(c - b))) / 2
            exit_in, entry_out = b + (a - b) * tangent / np.hypot(*(b - a)), b + (c - b) * tangent / np.hypot(*(c - b))
            straight = _densify(entry, exit_in, self._step)
            out.append(straight[1:])
            flags += [structure[i - 1]] * (len(straight) - 1)
            arc = _arc(exit_in, b, entry_out, self._step)
            out.append(arc[1:])
            flags += [structure[i - 1] and structure[i]] * (len(arc) - 1)     # structures are straight
            entry = entry_out
        points = np.concatenate(out)
        keep = np.concatenate([[True], np.hypot(*np.diff(points, axis=0).T) > 1e-6])
        return Alignment(points[keep], np.array(flags, dtype=bool)[keep[1:]])


def _corner_radius(a: Points, b: Points, c: Points) -> float:
    """Radius of the largest arc at corner b that fits between the middles of the legs (infinite if straight)."""
    u, v = b - a, c - b
    angle = abs(math.atan2(u[0] * v[1] - u[1] * v[0], float(u @ v)))
    if angle < 1e-9:
        return math.inf
    return min(float(np.hypot(*u)), float(np.hypot(*v))) / 2 / math.tan(angle / 2)


def _without_straight_points(p: Points) -> Points:
    """Points where the direction does not change are dropped."""
    if len(p) < 3:
        return p
    d = np.diff(p, axis=0)
    cross = d[:-1, 0] * d[1:, 1] - d[:-1, 1] * d[1:, 0]
    dot = (d[:-1] * d[1:]).sum(axis=1)
    keep = np.concatenate([[True], (np.abs(cross) > 1e-9) | (dot < 0), [True]])
    result: Points = p[keep]
    return result


def _douglas_peucker(p: Points, tolerance: float) -> Points:
    if len(p) < 3 or tolerance <= 0:
        return p
    a, b = p[0], p[-1]
    ab = b - a
    length = float(np.hypot(*ab))
    rel = p[1:-1] - a
    dist = np.abs(ab[0] * rel[:, 1] - ab[1] * rel[:, 0]) / length if length > 0 else np.hypot(*rel.T)
    k = int(np.argmax(dist))
    if dist[k] <= tolerance:
        return np.array([a, b])
    left = _douglas_peucker(p[: k + 2], tolerance)
    right = _douglas_peucker(p[k + 1:], tolerance)
    return np.concatenate([left[:-1], right])


def _densify(a: Points, b: Points, step: float) -> Points:
    n = max(1, math.ceil(float(np.hypot(*(b - a))) / step))
    f = np.linspace(0, 1, n + 1)[:, None]
    result: Points = a + (b - a) * f
    return result


def _arc(start: Points, corner: Points, end: Points, step: float) -> Points:
    """Circular arc from `start` to `end`, tangent to the legs meeting at `corner`."""
    u, v = corner - start, end - corner
    angle = abs(math.atan2(u[0] * v[1] - u[1] * v[0], float(u @ v)))
    tangent = float(np.hypot(*u))
    if angle < 1e-9 or tangent < 1e-9:
        return np.array([start, end])
    radius = tangent / math.tan(angle / 2)
    side = 1.0 if u[0] * v[1] - u[1] * v[0] > 0 else -1.0
    normal = np.array([-u[1], u[0]]) / tangent * side
    centre = start + normal * radius
    a0 = math.atan2(*(start - centre)[::-1])
    n = max(1, math.ceil(radius * angle / step))
    angles = a0 + side * angle * np.linspace(0, 1, n + 1)
    result: Points = centre + radius * np.column_stack([np.cos(angles), np.sin(angles)])
    return result
