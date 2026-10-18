# ADR 0018: Road structures - where (route search) and how (construction plan)

Date: 2026-10-18 - Status: accepted; refines ADR 0016

## Context

Game test of the road network (ADR 0016) on the Brenner map: the courses fit, but bridges and tunnels look bad and
the roads on mountain flanks go up and down. Measured on the plan (133 km): 52 bridges, 45 of them steeper than 8 %,
41 with a varying grade (waves in the deck), the longest 1.4 km climbing a slope; 3 of 7 tunnels shorter than 100 m;
1.8 changes of the grade's sign per km. Causes: structure moves of the route search up to the road's maximum grade
(20 %) at a cost per metre independent of the height; the profile made a bridge wherever the smoothed road lay 6 m
above the ground (also in curves) and merged them; decks followed the smoothed ground; the route cost only the
steepness of each move, not going up and down; the profile was smoothed over 40 m only.

## Decision

**Where** a structure can be is decided by the route search; **how** it is built by the construction plan.

- Route search (`roads/lattice.py`): a straight structure move must contain a bridge (at least 10 m above the ground, or
  over the wet zone) or a tunnel (at least 12 m below); its other steps are fills or cuts. Straight moves with fills and
  cuts only are not allowed: they mark only their end cells as road, so later routes did not bundle with them and ran
  beside them (tried: 174 instead of 72 sections on the Innsbruck example); the normal road gets its fills and cuts from
  the profile. Costs per metre: bridge = base + factor x height; fill and cut by their height or depth, cheaper than
  bridges and tunnels; tunnels at least 150 m long. Bridges and tunnels at most 6 % steep (they cross, the road climbs
  on the ground). Every metre of height gained or lost costs (`climb_weight`): steady grades, a curve more instead of up
  and down.
- Construction plan (`roads/profile.py`): bridges and tunnels only on the structure legs of the route search and over
  the wet zone, never on curved normal road (an arc belongs to a structure only between two structure legs). A span (leg
  or wet zone, extended by at most 60 m to where the road is back within fill and cut of the ground: the legs end on the
  25 m cells, e.g. just inside a gorge) is a straight line at a constant grade; the approaches are fitted to it within
  the grade limit and smoothed. The normal road follows a long smooth profile (120 m) within 10 m fill and 12 m cut; the
  heightmap is shaped to it. Too short bridges become fills, too short tunnels cuts.

## Alternatives

- Tuning the old thresholds only: the profile would still invent structures in curves and follow the ground in the
  deck.
- An optimisation of the whole vertical alignment (quadratic program with grade and curvature limits): better curves,
  but a solver dependency and slower; the spans as fixed straight lines give most of the effect.

## Consequences

- New parameters of `roads.network`: `climb_weight`, `bridge_height_cost`, `fill_cost`, `cut_cost`,
  `structure_max_grade`, `min_bridge_game_m` (was `min_structure_game_m`), `min_tunnel_game_m`,
  `approach_smooth_game_m`; changed defaults: bridge from 10 m, tunnel from 12 m, bridge cost 10, tunnel cost 12,
  profile smoothing 120 m.
- Fills and cuts up to 10 / 12 m are worked into the heightmap; larger differences only occur on spans.
- Open: road ends that look like dead ends in game (spurs to industries, ends in front of towns).
