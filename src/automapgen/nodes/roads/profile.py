"""Longitudinal profile of a road line and its structures.

Where a bridge or tunnel can be is decided by the route search (its straight structure legs) and by the wet zone;
this module plans how the road is built:
1. The normal road follows a long, smooth, grade-limited guide (`lines.road_profile`) that stays within a fill of
   `bridge_from_m` and a cut of `tunnel_from_m` of the ground; the terrain is shaped to it (fills and cuts).
2. Every span (structure leg or wet zone, extended to where the guide is back within fill and cut of the ground -
   the legs end on the cells of the search grid, e.g. just inside a gorge) is one straight line at a constant grade
   (at most `structure_max_grade`), raised to the clearance over water and riverbeds.
3. The approaches are fitted to the fixed spans within the grade limit and smoothed.
4. Inside a span the road is a bridge where it lies at least `bridge_from_m` above the ground (always over the wet
   zone), a tunnel where it lies at least `tunnel_from_m` below it, else a fill or a cut; short gaps between two
   structures of one kind are closed, too short bridges and tunnels become fills and cuts.
Heights and lengths in game metres; game height 0 = the surface of real water.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import gaussian_filter1d

from automapgen.nodes.lines import road_profile

NORMAL, BRIDGE, TUNNEL = 0, 1, 2
KIND_NAMES = ("normal", "bridge", "tunnel")

type Floats = NDArray[np.float64]
type Kinds = NDArray[np.int8]
type Flags = NDArray[np.bool_]


@dataclass(frozen=True)
class ProfileRules:
    max_grade: float
    smooth_m: float               # the guide of the normal road
    approach_smooth_m: float      # the approaches after fitting them to the spans
    bridge_from_m: float          # bridge from this height above the ground (below: a fill)
    tunnel_from_m: float          # tunnel from this depth below the ground (above: a cut)
    min_bridge_m: float
    min_tunnel_m: float
    max_extend_m: float           # a span grows at most this far over the ground beyond fill or cut
    max_gap_m: float              # normal pieces between two structures of one kind up to this length are closed
    clearance_m: float            # over water and riverbeds (above the surface of real water, game height 0)
    structure_max_grade: float    # spans at most this steep


@dataclass(frozen=True)
class Profile:
    """Road height per sample and the kind of every segment between two samples."""

    height: Floats
    kinds: Kinds


class ProfilePlanner:
    def __init__(self, rules: ProfileRules) -> None:
        self._r = rules

    def plan(self, ground: Floats, step_m: Floats, crossing: Flags, zone: Flags, water: Flags) -> Profile:
        """`ground` per sample, `step_m` = length of each segment, `crossing` = segment on a structure leg of the
        route search, `zone` = sample in the wet zone, `water` = sample on water or riverbed."""
        r = self._r
        wet = zone[:-1] | zone[1:]
        mean_step = float(step_m.mean()) if len(step_m) else 1.0
        spans = _runs(crossing | wet)
        height = self._guide(ground, step_m, spans, mean_step)
        spans = self._extended(spans, height - ground, step_m)
        height = self._guide(ground, step_m, spans, mean_step)
        normal = np.ones(len(ground), dtype=bool)
        for a, b in spans:
            normal[a: b + 2] = False
        band = np.clip(height, ground - r.tunnel_from_m, ground + r.bridge_from_m)      # fills and cuts only
        height = np.where(normal, band, height)
        height = road_profile(height, mean_step, 0, r.max_grade)
        floor = np.where(water, np.maximum(ground, 0) + r.clearance_m, -np.inf)
        fixed = np.zeros(len(ground), dtype=bool)
        for a, b in spans:
            height[a: b + 2] = self._deck(step_m[a: b + 1], height[a], height[b + 1], floor[a: b + 2])
            fixed[a: b + 2] = True
        height = self._approaches(height, fixed, step_m, mean_step)
        kinds = self._kinds(height - ground, spans, wet)
        return Profile(height, self._tidy(kinds, step_m, wet))

    def _guide(self, ground: Floats, step_m: Floats, spans: list[tuple[int, int]], mean_step: float) -> Floats:
        """The ground, straight across the spans, smoothed and grade-limited."""
        guide = ground.copy()
        for a, b in spans:
            guide[a: b + 2] = _line(step_m[a: b + 1], guide[a], guide[b + 1])
        return road_profile(guide, mean_step, self._r.smooth_m / mean_step, self._r.max_grade)

    def _extended(self, spans: list[tuple[int, int]], lift: Floats, step_m: Floats) -> list[tuple[int, int]]:
        """Spans (segment runs) grown over the neighbouring samples where the guide lies beyond a fill or a cut;
        overlapping spans merged."""
        beyond = (lift >= self._r.bridge_from_m) | (lift <= -self._r.tunnel_from_m)
        last = len(lift) - 2                                    # index of the last segment
        grown: list[tuple[int, int]] = []
        for a, b in spans:
            start, end, reach = a, b, self._r.max_extend_m
            while a > 0 and beyond[a] and step_m[a - 1: start].sum() < reach:
                a -= 1
            while b < last and beyond[b + 1] and step_m[end + 1: b + 2].sum() < reach:
                b += 1
            if grown and a <= grown[-1][1] + 1:
                grown[-1] = (grown[-1][0], max(b, grown[-1][1]))
            else:
                grown.append((a, b))
        return grown

    def _deck(self, step_m: Floats, start: float, end: float, floor: Floats) -> Floats:
        """A straight span at a constant grade between the guide heights at its ends, not steeper than allowed and
        at least `floor` everywhere."""
        length = float(step_m.sum())
        rise = self._r.structure_max_grade * length
        if abs(end - start) > rise:
            mid = (start + end) / 2
            start, end = mid - np.sign(end - start) * rise / 2, mid + np.sign(end - start) * rise / 2
        deck = _line(step_m, start, end)
        return deck + max(0.0, float(np.max(floor - deck)))

    def _approaches(self, height: Floats, fixed: Flags, step_m: Floats, mean_step: float) -> Floats:
        """The normal road fitted to the fixed spans within the grade limit, smoothed, fitted again."""
        rise = step_m * self._r.max_grade
        fitted = _within_grade(height, fixed, rise)
        smooth = gaussian_filter1d(fitted, self._r.approach_smooth_m / mean_step, mode="nearest") \
            if self._r.approach_smooth_m > 0 else fitted
        return _within_grade(np.where(fixed, height, smooth), fixed, rise)

    def _kinds(self, lift: Floats, spans: list[tuple[int, int]], wet: Flags) -> Kinds:
        """Structures only inside the spans: bridges high above the ground and over the wet zone, tunnels deep below."""
        segment_lift = (lift[:-1] + lift[1:]) / 2
        kinds = np.full(len(segment_lift), NORMAL, dtype=np.int8)
        inside = np.zeros(len(segment_lift), dtype=bool)
        for a, b in spans:
            inside[a: b + 1] = True
        kinds[inside & (segment_lift >= self._r.bridge_from_m)] = BRIDGE
        kinds[inside & (segment_lift <= -self._r.tunnel_from_m)] = TUNNEL
        kinds[wet] = BRIDGE
        return kinds

    def _tidy(self, kinds: Kinds, step_m: Floats, wet: Flags) -> Kinds:
        """Closes short normal gaps between structures of one kind; too short bridges and tunnels (not over water)
        become fills and cuts."""
        r = self._r
        out = kinds.copy()
        for a, b in _runs(out == NORMAL):
            if a > 0 and b < len(out) - 1 and out[a - 1] == out[b + 1] and step_m[a: b + 1].sum() <= r.max_gap_m:
                out[a: b + 1] = out[a - 1]
        for kind, shortest in ((BRIDGE, r.min_bridge_m), (TUNNEL, r.min_tunnel_m)):
            for a, b in _runs(out == kind):
                if step_m[a: b + 1].sum() < shortest and not wet[a: b + 1].any():
                    out[a: b + 1] = NORMAL
        return out


def _line(step_m: Floats, start: float, end: float) -> Floats:
    """Heights at constant grade from `start` to `end` at the samples of the steps."""
    s = np.concatenate([[0.0], np.cumsum(step_m)])
    result: Floats = np.interp(s, [0.0, s[-1]], [start, end])
    return result


def _within_grade(height: Floats, fixed: Flags, rise: Floats) -> Floats:
    """`height` with the free samples clipped so that no segment climbs more than its `rise` away from the fixed
    samples (upper and lower envelope of the fixed heights; where they conflict, their mean)."""
    if not fixed.any():
        return height
    upper = np.where(fixed, height, np.inf)
    lower = np.where(fixed, height, -np.inf)
    for i in range(1, len(height)):
        upper[i] = min(upper[i], upper[i - 1] + rise[i - 1])
        lower[i] = max(lower[i], lower[i - 1] - rise[i - 1])
    for i in range(len(height) - 2, -1, -1):
        upper[i] = min(upper[i], upper[i + 1] + rise[i])
        lower[i] = max(lower[i], lower[i + 1] - rise[i])
    clipped = np.clip(height, lower, upper)
    result: Floats = np.where(fixed, height, np.where(lower > upper, (lower + upper) / 2, clipped))
    return result


def _runs(mask: Flags) -> list[tuple[int, int]]:
    """(first, last) index of every run of True."""
    if not mask.any():
        return []
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1) - 1
    return list(zip(starts.tolist(), ends.tolist(), strict=True))
