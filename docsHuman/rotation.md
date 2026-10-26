# Arbitrary map bearings

`rotation_deg` is the direction at the **top of the game map**, clockwise from real-world north:
`0` = north, `45` = northeast, `90` = east, `180` = south, `270` = west. Decimal angles such as `32.5` work too.
Use a finite value from 0 (inclusive) to 360 (exclusive).

## Choose a region

For a new rotated map, specify its centre, game dimensions, scale and bearing:

```toml
graph = "map"
extends = ["../presets/alpine_realistic.toml"]

[params]
map_name = "Innsbruck_NE"
center = { lon = 11.39, lat = 47.27 }
game_width_km = 11.264
game_height_km = 22.528
scale = 2
rotation_deg = 45
dem_download = { dem_type = "GEDTM30" }
```

This covers a rectangle approximately 22.528 x 45.056 real km, with its top pointing northeast. As usual, output
resolution is the game dimension in metres divided by 4, plus 1. Terrain, land cover, OSM features and latitude-based
climate transitions use the same map frame. Do not rotate the exported files separately.

In the GUI, choose a **Map-top bearing** preset under Location (0°, 90°, 180° or 270°), or move the horizontal
**Rotation** slider in whole-degree steps. Moving the slider selects **Custom**, even at a cardinal angle. Choosing
Custom alone keeps the current angle. The **Exact bearing** field also steps in whole degrees. Loading an existing
project preserves its exact bearing rather than rounding it to the slider's steps. The selection polygon and map-top marker are calculated
by the backend using the same geometry as generation. Drag the polygon or double-click to choose its centre.
The GUI saves metric selections with `center`, not their enclosing download rectangle.

## Existing projects and compatibility

- **No `center`, cardinal bearing (0/90/180/270):** existing `crop` behaviour and raster processing are unchanged.
  The crop defines the region, including any existing scale/aspect mismatch.
- **No `center`, non-cardinal bearing:** the midpoint of `crop` becomes the centre. **The crop's width and height no
  longer define the output extent**; game dimensions and scale do. This lets an existing project opt in simply by
  changing its bearing. Inspect the footprint before generating.
- **Explicit `center`:** metric geometry is used at every bearing, including cardinal bearings. `center` takes
  precedence over `crop`. To return to legacy behaviour, remove `center` and specify a cardinal `crop` selection.

Metric geometry uses a local WGS84 azimuthal-equidistant projection. The bearing is relative to true north **at the
centre**; distortion increases away from the centre, as with any local planar map of the Earth. Selections crossing
the antimeridian are not supported and are rejected.

## Source data and edges

The output rectangle is distinct from the axis-aligned envelope needed to fetch data. At 45 degrees the envelope
of a square has roughly twice its area; a 1:5 rectangle needs about 3.6 times its area. Expect larger downloads,
more source-data memory and potentially more WorldCover/OSM regions. Output dimensions do not change.

OpenTopography receives the envelope with an interpolation margin (at least 200 m for metric selections). For a
local `dem_path`, supply a georeferenced file covering the entire rotated footprint, preferably with a margin.
Missing corners are an error: the generator will not stretch edge terrain to cover them. No-data regions connected
to the source-window edge are also treated as missing coverage if they reach the map. Enclosed internal DEM no-data
holes are still filled from nearby valid terrain.

The GUI's local-file coverage check is approximate (geographic envelopes); generation checks the actual footprint
in source pixel coordinates. For metric selections the GUI does not estimate minimum/maximum height from the larger
source envelope: leave the reference height empty to use the actual generated map's minimum, reported during the run.

## Custom graphs

The built-in `map`, `map_parcels` and `map_roads` graphs wire these settings automatically. In a custom graph:

- Connect `map.format` to the optional `map_format` input of `elevation.read`, as well as to `elevation.to_map` and
  `elevation.read_on_map`.
- Send the same `rotation_deg` to all three elevation nodes, and the same `center` (or fallback `crop`) to both
  readers. The readers derive an identical target grid, independent of source-window rounding.
- `elevation.to_map` uses the frame carried by the source. Setting an arbitrary angle on this node alone is rejected:
  the reader needs to know the footprint before cropping/downloading.

Old cached frame JSON remains readable. Changed elevation nodes and their parameters invalidate downstream cache
keys automatically; no manual cache deletion is needed.
