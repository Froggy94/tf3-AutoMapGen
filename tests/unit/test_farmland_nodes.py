"""Field nodes on small synthetic maps: OSM fields, crop plan, gap closing, parcels, field paths."""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError
from rasterio.crs import CRS

from automapgen.graph import NodeContext
from automapgen.nodes import farmland
from automapgen.nodes.maps import MapFrame, Rotation, SourceBounds
from automapgen.nodes.osm import Area, OsmAreas
from automapgen.nodes.rasters import ClassMap, Field, Labels, Mask

LOG: list[str] = []
CONTEXT = NodeContext("test", LOG.append, lambda f: None, lambda: False)
# 201 x 201 px heightmap (800 x 800 game m) over a small square near 47° N
FRAME = MapFrame(201, 201, SourceBounds(11.0, 47.0, 11.01, 47.0068, True), Rotation.NORTH,
                 CRS.from_epsg(4326).to_wkt())


def _square(x0: float, y0: float, size: float, tags: dict[str, str] | None = None) -> Area:
    """A field as a lon/lat square, x0/y0/size as fractions of the map."""
    lon = [11.0 + 0.01 * v for v in (x0, x0 + size)]
    lat = [47.0068 - 0.0068 * v for v in (y0, y0 + size)]
    ring = ((lon[0], lat[0]), (lon[1], lat[0]), (lon[1], lat[1]), (lon[0], lat[1]), (lon[0], lat[0]))
    return Area(tags or {"landuse": "farmland"}, (ring,), ())


def test_crop_plan_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError, match="no crop keys"):
        farmland.CropPlan(shares={"wheat": 1.0}, by_size=(farmland.CropSizeClass(min_game_ha=0, shares={"rice": 1}),))


def test_osm_fields_group_and_choose_crops() -> None:
    areas = OsmAreas((_square(0.1, 0.1, 0.3, {"landuse": "farmland", "crop": "maize"}), _square(0.5, 0.5, 0.15),
                      _square(0.66, 0.5, 0.05)))
    no_cover = Mask(np.zeros((201, 201), dtype=bool), FRAME)

    result = farmland.osm_fields(areas, no_cover, farmland.OsmFieldParams(), CONTEXT)
    preview = farmland.osm_preview(result.crop_type, result.groups, result.group_count, result.fields,
                                   farmland.PreviewParams())

    tagged = result.crop_type.values[40, 40]
    assert result.fields.values[40, 40]
    assert not result.fields.values[0, 0]
    assert tagged == list(farmland.CropPlan().shares).index("corn") + 1   # OSM crop=maize -> corn
    assert result.groups.values[105, 115] == result.groups.values[105, 138]   # the small field joined its neighbour
    assert result.group_count == 4
    assert preview.pixels.shape == (101, 101, 3)


def test_osm_fields_leave_water_free() -> None:
    no_cover = Mask(np.ones((201, 201), dtype=bool), FRAME)

    result = farmland.osm_fields(OsmAreas((_square(0.1, 0.1, 0.5),)), no_cover, farmland.OsmFieldParams(), CONTEXT)

    assert not result.fields.values.any()


def test_close_gap_turns_narrow_strips_into_cropland() -> None:
    crop = np.zeros((201, 201), dtype=bool)
    crop[50:150, 50:98] = crop[50:150, 103:150] = True        # two fields with a 5 px (20 m) meadow strip
    meadow = np.zeros_like(crop)
    meadow[50:150, 98:103] = True
    keep = np.zeros_like(crop)

    closed = farmland.close_gap(Mask(crop, FRAME), Mask(meadow, FRAME), Mask(keep, FRAME),
                                farmland.GapParams(gap_game_m=40), CONTEXT)
    kept = farmland.close_gap(Mask(crop, FRAME), Mask(meadow, FRAME), Mask(meadow, FRAME),
                              farmland.GapParams(gap_game_m=40), CONTEXT)

    assert closed.cropland.values[100, 100]
    assert not closed.other.values[100, 100]
    assert not kept.cropland.values[100, 100]                   # `keep` (e.g. settlements) stays


def test_parcels_and_field_paths() -> None:
    crop = np.zeros((201, 201), dtype=bool)
    crop[20:180, 20:180] = True                                 # 640 x 640 game m of cropland
    heights = Field(np.zeros((201, 201), dtype=np.float32), FRAME)

    result = farmland.parcels(Mask(crop, FRAME), heights, farmland.ParcelParams(), CONTEXT)
    paths = farmland.field_paths(result.parcels, farmland.FieldPathParams(), CONTEXT)

    assert result.parcels.values.max() > 4                      # divided into several parcels
    assert not result.parcels.values[0, 0]
    assert result.farm_grid.data["blocks"]
    assert set(np.unique(result.crop_type.values[crop])) <= {0, 1, 2, 3}
    assert paths.pixels.min() < 127 < paths.pixels.max()        # both sides of the borders


def test_field_paths_without_parcels() -> None:
    empty = Labels(np.zeros((201, 201), dtype=np.int32), FRAME)

    assert np.all(farmland.field_paths(empty, farmland.FieldPathParams(), CONTEXT).pixels == 255)


def test_crop_types_from_raster_parcels_follow_the_shares() -> None:
    crop = np.ones((201, 201), dtype=bool)
    params = farmland.ParcelParams(crops=farmland.CropPlan(shares={"wheat": 1.0}, by_size=(), osm_tags={}))

    result = farmland.parcels(Mask(crop, FRAME), Field(np.zeros((201, 201)), FRAME), params, CONTEXT)

    assert set(np.unique(result.crop_type.values[result.parcels.values > 0])) == {1}
    assert isinstance(result.crop_type, ClassMap)
