"""Longitudinal profile of a road line and its structures.

The profile follows a guide line - the ground, straight across the structure legs of the route search and across
the wet zone - smoothed and grade-limited (`lines.road_profile`), then raised to the clearance over water and
riverbeds (the approaches rise with it, within the grade limit). Where it lies far above the ground the road
becomes a bridge, far below a tunnel; the wet zone is always bridged; short normal gaps between two structures of
the same kind are closed, too short structures dropped (the terrain is shaped there instead). Heights and lengths
in game metres; game height 0 = the surface of real water.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from automapgen.nodes.lines import road_profile

NORMAL, BRIDGE, TUNNEL = 0, 1, 2
KIND_NAMES = ("normal", "bridge", "tunnel")

type Floats = NDArray[np.float64]
type Kinds = NDArray[np.int8]


@dataclass(frozen=True)
class ProfileRules:
    max_grade: float
    smooth_m: float
    bridge_from_m: float          # bridge where the road lies this much above the ground
    tunnel_from_m: float          # tunnel where it lies this much below
    min_structure_m: float
    max_gap_m: float              # normal pieces between two structures of one kind up to this length are closed
    clearance_m: float            # over water and riverbeds (above the surface of real water, game height 0)


@dataclass(frozen=True)
class Profile:
    """Road height per sample and the kind of every segment between two samples."""

    height: Floats
    kinds: Kinds


class ProfilePlanner:
    def __init__(self, rules: ProfileRules) -> None:
        self._r = rules

    def plan(self, ground: Floats, step_m: Floats, crossing: NDArray[np.bool_], zone: NDArray[np.bool_],
             water: NDArray[np.bool_]) -> Profile:
        """`ground` per sample, `step_m` = length of each segment, `crossing` = segment on a structure leg of the
        route search, `zone` = sample in the wet zone, `water` = sample on water or riverbed."""
        r = self._r
        guide = ground.copy()
        span = crossing | zone[:-1] | zone[1:]                 # straight across structure legs and the wet zone
        for a, b in _runs(span):
            guide[a: b + 2] = np.interp(np.cumsum(np.concatenate([[0], step_m[a: b + 1]])),
                                        [0, step_m[a: b + 1].sum()], [guide[a], guide[b + 1]])
        mean_step = float(step_m.mean()) if len(step_m) else 1.0
        height = road_profile(guide, mean_step, r.smooth_m / mean_step, r.max_grade)
        floor = np.where(water, np.maximum(ground, 0) + r.clearance_m, -np.inf)
        height = _raised(height, floor, step_m * r.max_grade)
        lift = height - ground
        segment_lift = (lift[:-1] + lift[1:]) / 2
        kinds = np.full(len(step_m), NORMAL, dtype=np.int8)
        kinds[segment_lift > r.bridge_from_m] = BRIDGE
        kinds[segment_lift < -r.tunnel_from_m] = TUNNEL
        wet = zone[:-1] | zone[1:]
        kinds[wet] = BRIDGE
        kinds = self._tidy(kinds, step_m, wet)
        return Profile(height, kinds)

    def _tidy(self, kinds: Kinds, step_m: Floats, wet: NDArray[np.bool_]) -> Kinds:
        """Closes short normal gaps between structures of one kind, drops too short structures (not over water)."""
        r = self._r
        out = kinds.copy()
        for a, b in _runs(out == NORMAL):
            if a > 0 and b < len(out) - 1 and out[a - 1] == out[b + 1] and step_m[a: b + 1].sum() <= r.max_gap_m:
                out[a: b + 1] = out[a - 1]
        for kind in (BRIDGE, TUNNEL):
            for a, b in _runs(out == kind):
                if step_m[a: b + 1].sum() < r.min_structure_m and not wet[a: b + 1].any():
                    out[a: b + 1] = NORMAL
        return out


def _raised(height: Floats, floor: Floats, rise: Floats) -> Floats:
    """`height` at least `floor`; neighbours raised so that no segment climbs more than its `rise`."""
    out = np.maximum(height, floor)
    for i in range(1, len(out)):
        out[i] = max(out[i], out[i - 1] - rise[i - 1])
    for i in range(len(out) - 2, -1, -1):
        out[i] = max(out[i], out[i + 1] - rise[i])
    return out


def _runs(mask: NDArray[np.bool_]) -> list[tuple[int, int]]:
    """(first, last) index of every run of True."""
    if not mask.any():
        return []
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1) - 1
    return list(zip(starts.tolist(), ends.tolist(), strict=True))
