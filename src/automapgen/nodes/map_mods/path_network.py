"""Path network for the path street mod: the path plan (node paths.plan) -> sections the game can build as streets.

Junctions: a path end on another path (branch) or two crossing paths are split at a shared point; pieces between
junctions are joined to long sections. Junctions with acute angles or more than four arms lose their shortest arms.
Gaps stay near planned roads, near other paths (except at the own junction) and at unconnected ends that meet another
path. Curves: tight hairpins give lanes of length 0 (game crash "MakeStraightEdge/MakeArcEdge result.length > 0"), so
the line is smoothed and the nodes are set so that the bend between two edges stays small; sharper bends become gaps.
All lengths in game metres.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict
from scipy.ndimage import gaussian_filter1d, maximum_filter1d
from scipy.spatial import cKDTree

type Points = NDArray[np.float64]
type Line = tuple[str, Points]                    # path kind, points in game metres
type Branch = tuple[int, int]                     # (section index, end 0/1)


class _Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class PathNetworkParams(_Value):
    gap_game_m: float
    """Gap around branches and crossings (distance to another path or a planned road)."""
    min_run_game_m: float
    """Shorter sections are left out (stubs)."""
    min_arm_game_m: float
    """Arms at junctions are left out only below this length."""
    snap_game_m: float
    """A path end this close to another path becomes a branch there."""
    cross_game_m: float
    """Two paths this close become a crossing."""
    min_junction_deg: float
    """More acute angles between two arms: the shorter arm is detached (gap)."""
    mixed_junctions: bool
    """Junctions between field tracks and hiking paths (different street types)."""


class CurveParams(_Value):
    max_edge_game_m: float
    """Split longer edges (paths are curvy)."""
    min_radius_game_m: float
    """Smallest curve radius: tighter places (hairpins) are smoothed locally."""
    relax_iterations: int
    """Passes of the local smoothing."""
    smooth_game_m: float
    """Smoothing before rounding against grid jags (Gauss); the ends stay exact."""
    min_curve_edge_game_m: float
    """Shorter edges allowed in curves (tight bends)."""
    max_turn_deg: float
    """Largest bend between two edges."""
    hard_turn_deg: float
    """A stronger bend stays anyway -> gap there."""
    chord_tol_game_m: float
    """An edge follows the line with at most this deviation."""


class BuildArea(_Value):
    """A test section of the map: only the paths inside are built."""

    center_uv: tuple[float, float]
    """Centre (u, v) of a test section of the map."""
    half_game_m: float
    """Half the edge length of the test section."""


def densify(pts: Points, step: float) -> Points:
    out = [pts[0]]
    for a, b in pairwise(pts):
        n = max(1, math.ceil(np.hypot(*(b - a)) / step))
        out += [a + (b - a) * k / n for k in range(1, n + 1)]
    return np.array(out)


def turn_deg(a: Points, b: Points) -> float:
    c = (a @ b) / max(np.hypot(*a) * np.hypot(*b), 1e-9)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def polyline_length(pts: Any) -> float:
    return float(np.hypot(*np.diff(np.asarray(pts), axis=0).T).sum())


class MapBox:
    """The part of the map that is built (whole map or a test section), in game metres."""

    def __init__(self, low: Points, high: Points) -> None:
        self.low = low
        self.high = high

    @classmethod
    def of(cls, scale: Points, test_area: BuildArea | None) -> MapBox:
        if test_area is None:
            return cls(np.zeros(2), scale)
        centre = np.array(test_area.center_uv) * scale
        return cls(centre - test_area.half_game_m, centre + test_area.half_game_m)

    def contains(self, p: Points) -> bool:
        return bool(np.all(p >= self.low) and np.all(p < self.high))


class PathClipper:
    """Paths inside the box (cut where they leave it), sampled every 2 m."""

    def __init__(self, scale: Points, box: MapBox) -> None:
        self._scale = scale
        self._box = box

    def lines(self, paths: Mapping[str, Sequence[Sequence[Sequence[float]]]], kinds: Sequence[str]) -> list[Line]:
        lines: list[Line] = []
        for kind in kinds:
            for line in paths.get(kind, []):
                run: list[Points] = []
                for p in densify(np.array(line) * self._scale, 2.0):
                    if self._box.contains(p):
                        run.append(p)
                    elif run:
                        if len(run) >= 2:
                            lines.append((kind, np.array(run)))
                        run = []
                if len(run) >= 2:
                    lines.append((kind, np.array(run)))
        return lines


    def road_points(self, roads: Sequence[Sequence[Sequence[float]]], margin: float) -> list[Points]:
        """Points of planned roads every 2 m, inside the box plus the margin."""
        out: list[Points] = []
        for road in roads:
            rp = densify(np.array(road) * self._scale, 2.0)
            out += list(rp[np.all((rp >= self._box.low - margin) & (rp < self._box.high + margin), axis=1)])
        return out


class JunctionCutter:
    """Cut indices per line: branches (end snapped onto the other path) and crossings (shared point)."""

    def __init__(self, params: PathNetworkParams) -> None:
        self._p = params

    def cuts(self, lines: list[Line], kinds: Sequence[str]) -> list[set[int]]:
        """Moves the snapped points in place."""
        groups = [list(kinds)] if self._p.mixed_junctions else [[k] for k in kinds]
        cuts: list[set[int]] = [set() for _ in lines]
        for group in groups:
            ids = [i for i, (k, _) in enumerate(lines) if k in group]
            if ids:
                self._group(lines, ids, cuts)
        return cuts

    def _group(self, lines: list[Line], ids: list[int], cuts: list[set[int]]) -> None:
        gpts = np.concatenate([lines[i][1] for i in ids])
        gline = np.concatenate([[i] * len(lines[i][1]) for i in ids])
        gidx = np.concatenate([np.arange(len(lines[i][1])) for i in ids])
        gtree = cKDTree(gpts)
        for i in ids:
            pts = lines[i][1]
            for e in (0, len(pts) - 1):
                best = None
                for j in gtree.query_ball_point(pts[e], self._p.snap_game_m):
                    if gline[j] != i:
                        d = np.hypot(*(gpts[j] - pts[e]))
                        if best is None or d < best[0]:
                            best = (d, gline[j], gidx[j])
                if best:
                    _, li, k = best
                    cuts[li].add(int(k))
                    pts[e] = lines[li][1][k]
        for a, b in gtree.query_pairs(self._p.cross_game_m):
            la, lb = gline[a], gline[b]
            if la == lb:
                continue
            ia, ib = gidx[a], gidx[b]
            na, nb = len(lines[la][1]), len(lines[lb][1])
            if min(ia, na - 1 - ia, ib, nb - 1 - ib) < 3:
                continue
            if any(abs(ia - c) < 10 for c in cuts[la]) or any(abs(ib - c) < 10 for c in cuts[lb]):
                continue
            cuts[la].add(int(ia))
            cuts[lb].add(int(ib))
            lines[lb][1][ib] = lines[la][1][ia]


@dataclass
class PieceGraph:
    """Pieces between cuts; nodes = shared end points (merged within 1 m)."""

    pieces: list[list[Any]]                       # [kind, points]
    node_pos: list[Points]
    piece_nodes: list[tuple[int, int]]
    degree: NDArray[np.int_]
    at_node: dict[int, list[int]]

    @classmethod
    def build(cls, lines: list[Line], cuts: list[set[int]]) -> PieceGraph:
        pieces: list[list[Any]] = []
        for i, (kind, pts) in enumerate(lines):
            cs = sorted(c for c in cuts[i] if 0 < c < len(pts) - 1)
            for a, b in zip([0, *cs], [*cs, len(pts) - 1], strict=True):
                if b > a:
                    pieces.append([kind, pts[a:b + 1].copy()])
        ends = np.array([p[1][e] for p in pieces for e in (0, -1)])
        node_of_end = [-1] * len(ends)
        node_pos: list[Points] = []
        if len(ends):
            etree = cKDTree(ends)
            for e in range(len(ends)):
                if node_of_end[e] < 0:
                    for j in etree.query_ball_point(ends[e], 1.0):
                        if node_of_end[j] < 0:
                            node_of_end[j] = len(node_pos)
                    node_pos.append(ends[e].copy())
        piece_nodes = [(node_of_end[2 * k], node_of_end[2 * k + 1]) for k in range(len(pieces))]
        for k, (n0, n1) in enumerate(piece_nodes):             # end points exactly on the nodes
            pieces[k][1][0], pieces[k][1][-1] = node_pos[n0], node_pos[n1]
        degree = np.zeros(len(node_pos), int)
        at_node: dict[int, list[int]] = {}
        for k, (n0, n1) in enumerate(piece_nodes):
            for n in (n0, n1):
                degree[n] += 1
                at_node.setdefault(n, []).append(k)
        return cls(pieces, node_pos, piece_nodes, degree, at_node)

    def joinable(self, n: int, kind: str) -> bool:
        ks = self.at_node[n]
        return (self.degree[n] == 2 and len(ks) == 2 and ks[0] != ks[1]
                and self.pieces[ks[0]][0] == self.pieces[ks[1]][0] == kind)

    def chains(self) -> list[list[Any]]:
        """Long sections: joined across nodes with exactly two pieces of the same kind. [kind, points, n0, n1]."""
        used = [False] * len(self.pieces)
        raw: list[list[Any]] = []
        for k in range(len(self.pieces)):
            if used[k]:
                continue
            used[k] = True
            kind = self.pieces[k][0]
            pts = list(self.pieces[k][1])
            n0, n1 = self.piece_nodes[k]
            for forward in (True, False):
                while True:
                    n = n1 if forward else n0
                    if not self.joinable(n, kind):
                        break
                    nxt = [q for q in self.at_node[n] if not used[q]]
                    if not nxt:
                        break
                    q = nxt[0]
                    used[q] = True
                    if self.piece_nodes[q][0] == n:
                        qp, other = list(self.pieces[q][1]), self.piece_nodes[q][1]
                    else:
                        qp, other = list(self.pieces[q][1][::-1]), self.piece_nodes[q][0]
                    if forward:
                        pts += qp[1:]
                        n1 = other
                    else:
                        pts = qp[::-1][:-1] + pts
                        n0 = other
            raw.append([kind, np.array(pts), n0, n1])
        return raw


class JunctionSelector:
    """Junctions = nodes with at least 3 section ends; acute angles and more than 4 arms are detached."""

    def __init__(self, min_junction_deg: float) -> None:
        self._min_deg = min_junction_deg

    def attach(self, raw_chains: list[list[Any]]) -> dict[Branch, int]:
        attached: dict[Branch, int] = {}
        branches: dict[int, list[Branch]] = {}
        for ci, c in enumerate(raw_chains):
            for e in (0, 1):
                branches.setdefault(c[2 + e], []).append((ci, e))
        for n, br in branches.items():
            if len(br) < 3:
                continue
            br.sort(key=lambda b: -polyline_length(raw_chains[b[0]][1]))
            keep: list[Branch] = []
            for b in br:
                d = self._direction(raw_chains[b[0]], b[1])
                if len(keep) < 4 and all(
                        np.degrees(np.arccos(np.clip(d @ self._direction(raw_chains[o[0]], o[1]), -1, 1)))
                        >= self._min_deg for o in keep):
                    keep.append(b)
            if len(keep) >= 3:
                for b in keep:
                    attached[b] = n
        for ci, c in enumerate(raw_chains):          # rings (start = end) never connected at both ends
            if (ci, 0) in attached and (ci, 1) in attached and c[2] == c[3]:
                del attached[(ci, 1)]
        return attached

    @staticmethod
    def _direction(c: list[Any], end: int) -> Points:
        pts = c[1] if end == 0 else c[1][::-1]
        k = min(len(pts) - 1, 6)
        d = pts[k] - pts[0]
        result: Points = d / (np.hypot(*d) or 1)
        return result


class CurveResampler:
    """Rounds corners, smooths slightly, then sets the nodes: edges up to max_edge, bend <= max_turn, ends exact.
    Result: pieces (split at bends that stay too sharp)."""

    def __init__(self, params: CurveParams) -> None:
        self._p = params

    def resample(self, run: Any) -> list[list[Points]]:
        p = self._p
        run = densify(np.asarray(run), 1.0)
        n = len(run)
        sig = p.smooth_game_m / 1.0
        if n > 3:
            sm = gaussian_filter1d(run, sig, axis=0, mode="nearest")
            k = np.arange(n)
            w = np.clip(np.minimum(k, n - 1 - k) / (2 * sig), 0, 1)[:, None]
            run = run * (1 - w) + sm * w
        run = densify(self._relax(run), 1.0)
        return self._split_sharp(self._nodes(run))

    def _nodes(self, run: Points) -> list[Points]:
        p = self._p
        n = len(run)
        out = [run[0]]
        last = 0      # index of the last node in run
        cand = None
        i = 1
        while i < n:
            v = run[i] - out[-1]
            length = float(np.hypot(*v))
            if length < p.min_curve_edge_game_m:
                i += 1
                continue
            mid = run[last + 1:i] - out[-1]            # deviation of the line from the edge out[-1] -> run[i]
            dev = float(np.max(np.abs(mid[:, 0] * v[1] - mid[:, 1] * v[0])) / length) if len(mid) else 0.0
            bent = dev > p.chord_tol_game_m or (len(out) >= 2 and turn_deg(out[-1] - out[-2], v) > p.max_turn_deg)
            if bent and cand is not None:
                out.append(run[cand])
                last, cand = cand, None
                continue
            if length >= p.max_edge_game_m or bent:
                out.append(run[i])
                last, cand = i, None
            else:
                cand = i
            i += 1
        if np.hypot(*(run[-1] - out[-1])) < p.min_curve_edge_game_m and len(out) > 1:
            out[-1] = run[-1]
        else:
            out.append(run[-1])
        return out

    def _split_sharp(self, out: list[Points]) -> list[list[Points]]:
        """Remaining sharp bends: leave the node out, split the line there."""
        pieces, cur = [], [out[0]]
        for k in range(1, len(out) - 1):
            if turn_deg(out[k] - out[k - 1], out[k + 1] - out[k]) > self._p.hard_turn_deg:
                if len(cur) >= 2:
                    pieces.append(cur)
                cur = [out[k + 1]] if k + 1 < len(out) - 1 else []
                continue
            if not cur or not np.array_equal(cur[-1], out[k]):
                cur.append(out[k])
        if cur and not np.array_equal(cur[-1], out[-1]):
            cur.append(out[-1])
        if len(cur) >= 2:
            pieces.append(cur)
        return pieces

    @staticmethod
    def radii(p: Points, k: int = 3) -> NDArray[np.float64]:
        """Curve radius per point from the points k before and after (circumcircle)."""
        r = np.full(len(p), np.inf)
        if len(p) < 2 * k + 1:
            return r
        a, b, c = p[:-2 * k], p[k:-k], p[2 * k:]
        ab, bc, ca = (np.hypot(*(b - a).T), np.hypot(*(c - b).T), np.hypot(*(a - c).T))
        cross = np.abs((b - a)[:, 0] * (c - a)[:, 1] - (b - a)[:, 1] * (c - a)[:, 0])
        r[k:-k] = np.where(cross > 1e-9, ab * bc * ca / (2 * np.maximum(cross, 1e-9)), np.inf)
        return r

    def _relax(self, p: Points) -> Points:
        """Where the curve radius is below min_radius, smooth locally (repeatedly); the ends stay fixed."""
        p = p.copy()
        win = np.ones(5) / 5
        for _ in range(self._p.relax_iterations):
            tight = self.radii(p) < self._p.min_radius_game_m
            if not tight.any():
                break
            zone = maximum_filter1d(tight, 21, mode="constant", cval=False)   # +-10 m around tight places (any length)
            zone[:2] = zone[-2:] = False
            sm = np.column_stack([np.convolve(np.pad(p[:, d], 2, mode="edge"), win, mode="valid") for d in (0, 1)])
            p[zone] = sm[zone]
        return p


@dataclass(frozen=True)
class PathSection:
    kind: str
    points: Sequence[Points]
    start_junction: int                           # node index or -1
    end_junction: int


@dataclass(frozen=True)
class PathNetwork:
    sections: Sequence[PathSection]               # longest first
    junction_count: int
    connected_ends: int
    gaps: int
    sharp_gaps: int                               # gaps at bends that stay too sharp

    def length_game_m(self, kind: str) -> float:
        return sum(polyline_length(s.points) for s in self.sections if s.kind == kind)

    @property
    def edge_count(self) -> int:
        return sum(len(s.points) - 1 for s in self.sections)


class PathNetworkPlanner:
    """Lines -> junctions, long sections, gaps, curves."""

    def __init__(self, network: PathNetworkParams, curves: CurveParams) -> None:
        self._p = network
        self._resampler = CurveResampler(curves)

    def plan(self, lines: list[Line], kinds: Sequence[str], road_points: Sequence[Points]) -> PathNetwork:
        """`road_points`: points of planned roads (gaps there), already in game metres."""
        cuts = JunctionCutter(self._p).cuts(lines, kinds)
        graph = PieceGraph.build(lines, cuts)
        raw_chains = graph.chains()
        attached = JunctionSelector(self._p.min_junction_deg).attach(raw_chains)
        all_pts = [p for c in raw_chains for p in c[1]]
        owner = [ci for ci, c in enumerate(raw_chains) for _ in c[1]]
        all_pts += list(road_points)
        owner += [-1] * len(road_points)
        tree = cKDTree(np.array(all_pts)) if all_pts else None
        owners = np.array(owner)
        sections: list[PathSection] = []
        gaps = sharp = 0
        for ci, (kind, pts, n0, n1) in enumerate(raw_chains):
            assert tree is not None
            free = self._free(ci, pts, n0, n1, attached, graph, tree, owners)
            gaps += int(np.sum(free[1:] < free[:-1]))
            sharp += self._sections(kind, pts, free, attached.get((ci, 0), -1), attached.get((ci, 1), -1), sections)
        sections.sort(key=lambda s: -len(s.points))
        return PathNetwork(sections, len(set(attached.values())), len(attached), gaps, sharp)

    def _free(self, ci: int, pts: Points, n0: int, n1: int, attached: dict[Branch, int], graph: PieceGraph,
              tree: cKDTree, owner: NDArray[np.int_]) -> NDArray[np.bool_]:
        gap = self._p.gap_game_m
        j0, j1 = attached.get((ci, 0), -1), attached.get((ci, 1), -1)
        exempt = [graph.node_pos[j] for j in (j0, j1) if j >= 0]
        free = np.ones(len(pts), bool)
        neighbours: Any = tree.query_ball_point(pts, gap)   # one index list per point
        for k, idx in enumerate(neighbours):
            if any(owner[j] != ci for j in idx) and not any(np.hypot(*(pts[k] - q)) < gap * 1.5 for q in exempt):
                free[k] = False
        for e, j, n in ((0, j0, n0), (len(pts) - 1, j1, n1)):   # unconnected end where other paths end: gap
            if j < 0 and graph.degree[n] >= 2:
                free[np.hypot(*(pts - pts[e]).T) < gap] = False
        return free

    def _sections(self, kind: str, pts: Points, free: NDArray[np.bool_], j0: int, j1: int,
                  out: list[PathSection]) -> int:
        sharp = 0
        k = 0
        while k < len(pts):
            if not free[k]:
                k += 1
                continue
            e = k
            while e < len(pts) and free[e]:
                e += 1
            run = pts[k:e]
            at_junction = (k == 0 and j0 >= 0) or (e == len(pts) and j1 >= 0)
            minimum = self._p.min_arm_game_m if at_junction else self._p.min_run_game_m
            if len(run) >= 2 and polyline_length(run) >= minimum:
                parts = self._resampler.resample(run)
                for pi, part in enumerate(parts):
                    first = pi == 0 and np.array_equal(part[0], run[0])
                    last = pi == len(parts) - 1 and np.array_equal(part[-1], run[-1])
                    if len(parts) > 1 and polyline_length(np.array(part)) < self._p.min_arm_game_m:
                        continue
                    out.append(PathSection(kind, part, j0 if k == 0 and first else -1,
                                           j1 if e == len(pts) and last else -1))
                if len(parts) > 1:
                    sharp += len(parts) - 1
            k = e
        return sharp


@dataclass(frozen=True)
class ScriptNetwork:
    """The network as texts for the game script: points (map fractions x 1e7), sections, kinds, junction points."""

    nodes: str
    chains: str
    kinds: str
    junctions: str

    @classmethod
    def encode(cls, network: PathNetwork, kinds: Sequence[str], scale: Points) -> ScriptNetwork:
        """Junction nodes share one point."""
        points: list[Points] = []
        chain_parts, kind_ids = [], []
        junction_point: dict[int, int] = {}
        kind_index = {k: i + 1 for i, k in enumerate(kinds)}
        for s in network.sections:
            ids = []
            for n, p in enumerate(s.points):
                j = s.start_junction if n == 0 else (s.end_junction if n == len(s.points) - 1 else -1)
                if j >= 0 and j in junction_point:
                    ids.append(junction_point[j])
                    continue
                points.append(p / scale)
                ids.append(len(points))
                if j >= 0:
                    junction_point[j] = len(points)
            chain_parts.append(" ".join(map(str, ids)))
            kind_ids.append(str(kind_index[s.kind]))
        return cls(" ".join(f"{round(u * 1e7)} {round(v * 1e7)}" for u, v in points), "|".join(chain_parts),
                   " ".join(kind_ids), " ".join(map(str, sorted(junction_point.values()))))
