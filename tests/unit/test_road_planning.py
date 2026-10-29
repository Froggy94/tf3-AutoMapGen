"""Road planning on synthetic terrain: heading lattice, alignment with arcs, profile with structures, the network."""

from __future__ import annotations

import itertools
import math

import numpy as np
from rasterio.crs import CRS

from automapgen.graph import NodeContext
from automapgen.nodes.images import Image
from automapgen.nodes.industries import IndustryList
from automapgen.nodes.maps import MapFormat, MapFrame, Rotation, SourceBounds
from automapgen.nodes.rasters import Field, Mask
from automapgen.nodes.roads import network
from automapgen.nodes.roads.alignment import AlignmentBuilder, Leg
from automapgen.nodes.roads.lattice import DIRECTIONS, LatticeCosts, LatticeGraph, Terrain, TurnRule
from automapgen.nodes.roads.profile import BRIDGE, NORMAL, TUNNEL, ProfilePlanner, ProfileRules
from automapgen.nodes.towns import TownList

COSTS = LatticeCosts(cell_m=25, min_radius_m=25, max_grade=0.1, slope_weight=2, turn_weight=0.3,
                     structure_steps=(2, 3, 4, 6, 8), max_structure_m=400, bridge_cost=4, tunnel_cost=8,
                     structure_fixed_m=100, climb_weight=3, bridge_from_m=10, tunnel_from_m=12, bridge_height_cost=0.5,
                     fill_cost=0.2, cut_cost=0.25, structure_max_grade=0.06, min_tunnel_m=150)
RULES = ProfileRules(max_grade=0.1, smooth_m=20, approach_smooth_m=20, bridge_from_m=10, tunnel_from_m=12,
                     min_bridge_m=20, min_tunnel_m=150, max_extend_m=60, max_gap_m=20, clearance_m=10,
                     structure_max_grade=0.06)


def _terrain(height: np.ndarray) -> Terrain:
    none = np.zeros(height.shape, dtype=bool)
    return Terrain(height.astype(float), np.ones(height.shape), none, none)


def test_turns_leave_room_for_the_minimum_radius() -> None:
    allowed = {h for h, _ in TurnRule(25, 25).allowed[0]}

    assert {DIRECTIONS[h] for h in allowed} == {(1, 0), (2, 1), (1, 1), (2, -1), (1, -1)}   # up to 45 degrees


def test_route_on_flat_ground_is_straight_and_normal() -> None:
    graph = LatticeGraph(_terrain(np.zeros((30, 30))), COSTS)

    result = graph.route((15, 2), [(15, 27)])

    assert result is not None
    steps, cost = result
    assert steps[0].start == (15, 2)
    assert steps[-1].end == (15, 27)
    assert not any(s.structure for s in steps)
    assert cost == 25 * 25                                             # 25 cells straight on


def test_route_bridges_a_gorge() -> None:
    height = np.zeros((30, 30))
    height[:, 13:17] = -80                                             # a gorge across the whole window

    result = LatticeGraph(_terrain(height), COSTS).route((15, 2), [(15, 27)])

    assert result is not None
    assert any(s.structure for s in result[0])


def test_route_keeps_a_steady_grade_instead_of_going_up_and_down() -> None:
    height = np.zeros((30, 30))
    height[15, 8:22] = 0                                               # a flat line ...
    height[10:15, 8:22] = 30                                           # ... between ridges the straight line crosses
    height[16:21, 8:22] = 30
    height[12:19, 10] = 30
    height[12:19, 19] = 30                                             # two humps on the straight line

    steps, _ = LatticeGraph(_terrain(height), COSTS).route((15, 2), [(15, 27)])   # type: ignore[misc]

    cells = [s.end for s in steps]
    climb = sum(abs(height[b] - height[a]) for a, b in itertools.pairwise([steps[0].start, *cells]))
    assert climb < 2 * 30                                              # not up and down over both humps


def test_arcs_keep_the_minimum_radius() -> None:
    legs = [Leg((0, 0), (100, 0), False), Leg((100, 0), (100, 100), False)]

    line = AlignmentBuilder(min_radius_m=25, straighten_m=0, step_m=4).build(legs)

    p = line.points
    assert np.allclose(p[0], [0, 0])
    assert np.allclose(p[-1], [100, 100])
    on_arc = np.abs(np.hypot(p[:, 0] - 50, p[:, 1] - 50) - 50) < 1e-6   # arc of radius 50 around (50, 50)
    assert on_arc.sum() > 10
    assert math.isclose(line.length_m, 50 + 50 * math.pi / 2 + 50, rel_tol=0.01)


def test_zigzag_is_straightened() -> None:
    pts = [(0, 0), (25, 0), (50, 12.5), (75, 12.5), (100, 25), (125, 25)]
    legs = [Leg(a, b, False) for a, b in itertools.pairwise(pts)]

    line = AlignmentBuilder(min_radius_m=25, straighten_m=10, step_m=4).build(legs)

    direction = np.diff(line.points, axis=0)
    angles = np.arctan2(direction[:, 1], direction[:, 0])
    assert np.ptp(angles) < 1e-6                                       # one straight line


def _straight(s: np.ndarray, half_m: float) -> np.ndarray:
    """A structure leg of the route search around the middle of the line (per segment)."""
    middle = (s[:-1] + s[1:]) / 2
    leg: np.ndarray = np.abs(middle - s[-1] / 2) < half_m
    return leg


def test_profile_bridges_valleys_and_tunnels_hills_on_the_structure_legs() -> None:
    s = np.arange(0, 1000.0, 4)
    steps = np.full(len(s) - 1, 4.0)
    none = np.zeros(len(s), dtype=bool)
    valley = np.where(np.abs(s - 500) < 60, -40.0, 0.0)
    hill = np.where(np.abs(s - 500) < 120, 60.0, 0.0)

    bridged = ProfilePlanner(RULES).plan(valley, steps, _straight(s, 100), none, none)
    tunnelled = ProfilePlanner(RULES).plan(hill, steps, _straight(s, 160), none, none)
    unplanned = ProfilePlanner(RULES).plan(valley, steps, np.zeros(len(steps), dtype=bool), none, none)

    middle = len(steps) // 2
    assert bridged.kinds[middle] == BRIDGE
    assert tunnelled.kinds[middle] == TUNNEL
    assert bridged.kinds[0] == tunnelled.kinds[0] == NORMAL
    assert (unplanned.kinds == NORMAL).all()                             # no structure the route search did not plan
    deck = bridged.height[bridged.kinds.repeat(1).nonzero()[0]]
    assert np.ptp(np.diff(deck)) < 1e-6                                  # straight: a constant grade
    assert np.max(np.abs(np.diff(bridged.height))) <= 0.1 * 4 + 1e-6   # grade limit


def test_short_tunnels_become_cuts_and_low_bridges_fills() -> None:
    s = np.arange(0, 1000.0, 4)
    steps = np.full(len(s) - 1, 4.0)
    none = np.zeros(len(s), dtype=bool)
    hill = np.where(np.abs(s - 500) < 40, 30.0, 0.0)                   # 80 m long: shorter than a tunnel may be
    dip = np.where(np.abs(s - 500) < 60, -8.0, 0.0)                    # 8 m deep: below a bridge's height

    cut = ProfilePlanner(RULES).plan(hill, steps, _straight(s, 100), none, none)
    fill = ProfilePlanner(RULES).plan(dip, steps, _straight(s, 100), none, none)

    assert (cut.kinds == NORMAL).all()
    assert (fill.kinds == NORMAL).all()


def test_profile_bridges_water_high_enough() -> None:
    s = np.arange(0, 800.0, 4)
    steps = np.full(len(s) - 1, 4.0)
    water = np.abs(s - 400) < 20
    zone = np.abs(s - 400) < 70                                            # water + 50 m margin
    ground = np.where(water, -3.0, 5.0)                                    # real water below its surface (0)

    profile = ProfilePlanner(RULES).plan(ground, steps, np.zeros(len(steps), dtype=bool), zone, water)

    assert (profile.kinds[(zone[:-1] | zone[1:])] == BRIDGE).all()        # the whole zone is bridged
    assert profile.height[water].min() >= 10 - 1e-6                       # 10 m above the water surface
    assert profile.kinds[0] == NORMAL
    assert np.max(np.abs(np.diff(profile.height))) <= 0.1 * 4 + 1e-6      # the approaches rise within the limit


def test_network_connects_towns_and_industries_through_the_terrain() -> None:
    n = 201                                                              # 800 x 800 game m, scale 1:1
    frame = MapFrame(n, n, SourceBounds(11.0, 47.0, 11.0106, 47.0072, True), Rotation.NORTH,
                     CRS.from_epsg(4326).to_wkt())
    x = np.arange(n) * 4.0
    dem = np.full((n, n), 600.0, dtype=np.float32)
    dem[:, np.abs(x - 400) < 30] = 560                                   # a valley between the towns
    empty = Mask(np.zeros((n, n), dtype=bool), frame)
    towns = TownList([{"name": "West", "x": -300.0, "y": 0.0, "capacity": 100},
                      {"name": "East", "x": 300.0, "y": 0.0, "capacity": 80}])
    industries = IndustryList([{"name": "Farm", "type": "farm", "x": 100.0, "y": -200.0}])   # east of the valley
    params = network.RoadNetworkParams(town_radius_game_m=60, min_bridge_game_m=10, preview_width=100)

    result = network.network(Field(dem, frame), empty, empty, empty, empty, empty, towns, industries,
                             MapFormat(0.8, 0.8, 1, 1), 500, Image(np.zeros((50, 50, 3), dtype=np.uint8)), params,
                             NodeContext("roads", lambda m: None, lambda f: None, lambda: False))
    plan = result.plan.data

    assert [t["name"] for t in plan["targets"]] == ["West", "East", "Farm"]
    assert {w["kind"] for w in plan["ways"]} == {"normal", "bridge"}       # the valley is bridged
    uses: dict[int, int] = {}
    for way in plan["ways"]:
        for i in (way["nodes"][0], way["nodes"][-1]):
            uses[i] = uses.get(i, 0) + 1
    assert max(uses.values()) >= 3                                       # the spur meets the link in a junction
    heights = [z for _, _, z in plan["nodes"]]
    assert min(heights) > 90                                             # (600 - 500) m, no way down into the valley
    assert result.core.values.any()
    assert result.preview.pixels.shape[1] == 100
