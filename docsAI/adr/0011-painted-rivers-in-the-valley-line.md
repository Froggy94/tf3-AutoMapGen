# ADR 0011: Painted rivers moved into the valley line

Date: 2026-10-14 - Status: accepted

## Context

Painted riverbeds were drawn exactly on the OSM course. Where OSM and the elevation model disagree (OSM a few metres
off, narrow gorges blurred in a 30 m model), the bed lay on the slope beside the valley (old Brenner map).
A diagnosis on Brenner and Innsbruck (GEDTM30) showed no systematic offset between OSM and the elevation model (best
global shift 0 m, per quarter of the map at most one pixel), but local errors: on 16 % of the painted course OSM lay
more than 3 m above the lowest point within 100 m to the side, on 7 % more than 5 m.

## Decision

A node `rivers.to_valley` after `rivers.plan` moves the painted runs of the plan; every consumer (water, painted
riverbed, groove, `_rivers.json`) uses the moved plan.
- Each run of painted segments is resampled every 2 heightmap pixels and moved sideways along its normals within a
  corridor (`max_move_real_m`, real metres, default 100).
- The move is chosen for the whole run at once by dynamic programming: cost = height + `lower_per_100m_m` per 100 m
  of move (OSM stays where the valley is flat), the move changes by at most `max_bend` per metre along the course,
  it is 0 at both ends (junctions, transition to real water), then it is averaged over `smooth_real_m`.
- Real water is not moved. A preview shows the moved courses coloured by the size of the move.

Result: more than 3 m above the corridor minimum on 2 % instead of 16 % of the course (Brenner and Innsbruck), the
course length unchanged (no zigzag).

## Alternatives

- **The lowest point per point** (the first idea) - jumps and zigzag, random wandering in flat valleys, side valleys.
- **A global or regional shift** of OSM - the diagnosis found no systematic offset.
- **Streams derived from the elevation model** (flow accumulation) - unreliable in flat valleys, much more complex.
- **Carving the valley under OSM** ("stream burning") - changes the real terrain visibly.

## Consequences

The golden-master project switches the move off (`max_move_real_m = 0`) to stay comparable with the legacy project.
Tributaries still end at the OSM junction; a moved main river can make the last metres of a tributary bend.
