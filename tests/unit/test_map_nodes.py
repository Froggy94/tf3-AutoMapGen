"""Built-in map nodes on small synthetic data (no files from outside, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from numpy.typing import NDArray
from rasterio.transform import from_bounds

from automapgen.graph import NodeContext
from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.compiler import GraphCompiler
from automapgen.graph.composer import GraphComposer
from automapgen.graph.library import FolderLibrary
from automapgen.graph.node import OutputTarget
from automapgen.graph.paramsets import ParameterSet
from automapgen.graphs import GRAPHS_DIR
from automapgen.nodes import BUILTIN_MODULES, classes, elevation, fields, files, images, masks
from automapgen.nodes.images import Image
from automapgen.nodes.maps import MapFormat, MapFormatParams, MapFrame, Rotation, SourceBounds, map_format
from automapgen.nodes.rasters import ClassMap, Field, Mask, RasterCodec

FRAME = MapFrame(9, 9, SourceBounds(0, 0, 800, 800, False), Rotation.NORTH, "")   # 100 m per pixel
LOG: list[str] = []
CONTEXT = NodeContext("test", LOG.append, lambda f: None, lambda: False)


def field(values: NDArray[np.float64] | list[list[float]]) -> Field:
    return Field(np.asarray(values), FRAME)


def test_map_format_and_frame() -> None:
    fmt = map_format(MapFormatParams(game_width_km=3.072, game_height_km=3.072, scale=2), CONTEXT)
    frame = MapFrame(769, 769, SourceBounds(11.0, 47.0, 11.1, 47.05, True), Rotation.EAST, "")

    assert (fmt.width_px, fmt.height_px, fmt.game_per_real) == (769, 769, 0.5)
    assert frame.extent_km[0] == pytest.approx(5.53, abs=0.01)   # sideways: map width = north-south extent
    assert frame.work_grid(4) == (193, 193)
    assert MapFrame.from_json(frame.to_json()) == frame


def test_height_scale_defaults_to_scale_and_logs_steeper_slopes() -> None:
    LOG.clear()
    same = map_format(MapFormatParams(scale=2), CONTEXT)
    assert (same.height_scale, same.height_game_per_real) == (2, 0.5)
    assert LOG == []

    real = map_format(MapFormatParams(scale=2, height_scale=1), CONTEXT)
    assert (real.game_per_real, real.height_game_per_real) == (0.5, 1.0)
    assert LOG == ["heights 1:1, distances 1:2 - slopes x2 in game (more bridges and tunnels, less building land for "
                   "towns)"]
    assert map_format(MapFormatParams(scale=2, height_scale=0.5), CONTEXT).height_game_per_real == 2
    assert MapFormat.from_json(real.to_json()) == real
    assert MapFormat.from_json({"game_width_km": 1, "game_height_km": 1, "scale": 2}).height_scale == 2


def test_rasters_are_read_only_and_survive_the_codec(tmp_path: Path) -> None:
    raster = field(np.arange(81, dtype=np.float64).reshape(9, 9))
    path = tmp_path / "f.npz"

    RasterCodec(lambda v, f: Field(v, f)).save(raster, path)
    loaded = RasterCodec(lambda v, f: Field(v, f)).load(path)

    with pytest.raises(ValueError, match="read-only"):
        raster.values[0, 0] = 1
    assert np.array_equal(loaded.values, raster.values)
    assert loaded.frame == FRAME
    assert raster.pixel_m == 100


def test_field_nodes() -> None:
    plane = field([[x * 10.0 for x in range(9)] for _ in range(9)])   # rises 10 m per 100 m

    coarse = fields.resample(plane, fields.ResampleParams(work_scale=3, method="box"))
    degrees = fields.slope(plane).values
    ramp = fields.ramp(field(np.full((9, 9), 40.0)), fields.RampParams(start=35, per_unit=20)).values
    total = fields.total((plane, plane, plane)).values
    lifted = fields.at_least(plane, 25.0).values
    noise = fields.noise(plane, fields.NoiseParams(seed=3, wave_m=200, amplitude=50))

    assert (coarse.width, coarse.height) == (3, 3)
    assert np.allclose(degrees, np.degrees(np.arctan(0.1)))
    assert np.all(ramp == 100)
    assert np.array_equal(total, plane.values * 3)
    assert lifted.min() == 25
    assert float(noise.values.std()) == pytest.approx(50, rel=1e-3)
    assert np.array_equal(noise.values, fields.noise(plane, fields.NoiseParams(seed=3, wave_m=200,
                                                                              amplitude=50)).values)
    with pytest.raises(ValueError, match="different size"):
        fields.total((plane, coarse))


def test_mask_nodes() -> None:
    plane = field([[float(x) for x in range(9)] for _ in range(9)])
    high = masks.above(plane, masks.LimitParams(limit=4))
    low = masks.below(plane, masks.LimitParams(limit=2))
    coarse = Mask(np.eye(3, dtype=bool), FRAME)

    assert int(high.values.sum()) == 4 * 9
    assert not masks.all_of((high, low)).values.any()
    assert int(masks.any_of((high, low)).values.sum()) == 6 * 9
    assert np.array_equal(masks.invert(high).values, ~high.values)
    assert masks.to_map(coarse).values.shape == (9, 9)
    assert not masks.smooth(Mask(np.eye(9, dtype=bool), FRAME), masks.MaskSmoothParams(radius_m=300)).values.any()


def test_class_nodes() -> None:
    plane = field([[float(x) for x in range(9)] for _ in range(9)])
    banded = classes.bands(plane, classes.BandParams(limits=[3, 6], first=1))
    speckled = ClassMap(np.where(np.eye(9, dtype=bool), 2, 1), FRAME)

    assert list(banded.values[0]) == [1, 1, 1, 2, 2, 2, 3, 3, 3]
    assert np.all(classes.majority(speckled, classes.MajorityParams(codes=[1, 2], radius_m=200)).values == 1)
    assert int(classes.select(banded, classes.SelectParams(codes=[1, 3])).values.sum()) == 6 * 9
    assert classes.to_map(ClassMap(np.ones((3, 3)), FRAME)).values.shape == (9, 9)


def test_image_nodes() -> None:
    codes = ClassMap(np.array([[1, 2], [0, 1]]), FRAME)
    grey = images.palette(codes, images.PaletteParams(colors={1: 63, 2: 127}))
    rgb = images.palette(codes, images.PaletteParams(colors={1: (10, 20, 30)}))
    tinted = images.tint(rgb, Mask(np.array([[True, False], [False, False]]), FRAME),
                         images.TintParams(color=(110, 120, 130), strength=0.5))

    assert grey.pixels.tolist() == [[63, 127], [0, 63]]
    assert tinted.pixels[0, 0].tolist() == [60, 70, 80]
    assert tinted.pixels[1, 1].tolist() == [10, 20, 30]
    assert images.from_mask(Mask(np.eye(2, dtype=bool), FRAME)).pixels.tolist() == [[255, 0], [0, 255]]
    with pytest.raises(ValueError, match="uint8/uint16"):
        Image(np.zeros((2, 2), dtype=np.float32))


@pytest.fixture
def geotiff(tmp_path: Path) -> Path:
    """A 4 x 6 px elevation model in metres (EPSG:32632) with one no-data pixel."""
    path = tmp_path / "dem.tif"
    heights = np.arange(24, dtype=np.float32).reshape(4, 6) * 10 + 500
    heights[0, 0] = -9999
    with rasterio.open(path, "w", driver="GTiff", width=6, height=4, count=1, dtype="float32", nodata=-9999,
                       crs="EPSG:32632", transform=from_bounds(0, 0, 600, 400, 6, 4)) as dst:
        dst.write(heights, 1)
    return path


def test_elevation_nodes(geotiff: Path) -> None:
    source = elevation.read(elevation.ReadParams(path=str(geotiff)), CONTEXT)
    on_map = elevation.to_map(source, MapFormat(0.04, 0.024, 2, 2), elevation.ToMapParams(rotation_deg=90), CONTEXT)
    refs = elevation.references(on_map.heights, elevation.ReferenceParams(min_height_m=None, water_sink_m=40),
                                CONTEXT)
    encoded = elevation.encode(on_map.heights, refs.black_m, elevation.EncodeParams(bit_depth=8))

    assert np.isnan(source.heights[0, 0])
    assert (on_map.frame.width_px, on_map.frame.height_px) == (11, 7)
    assert np.isfinite(on_map.heights.values).all()
    assert refs.black_m == refs.land_min_m - 40
    assert encoded.image.pixels.dtype == np.uint8
    assert encoded.white_m >= float(on_map.heights.values.max())
    with pytest.raises(ValueError, match="must be above black"):
        elevation.encode(on_map.heights, 10_000.0, elevation.EncodeParams())


def test_file_sinks(tmp_path: Path) -> None:
    context = NodeContext("sink", LOG.append, lambda f: None, lambda: False, OutputTarget(tmp_path / "out", "P"))

    files.png(Image(np.zeros((2, 2), dtype=np.uint16)), files.PngParams(name="_x.png"), context)
    files.height_range(500.0, 540.0, 2300.0, files.FileParams(name="_range.json"), context)

    assert (tmp_path / "out" / "P_x.png").is_file()
    assert json.loads((tmp_path / "out" / "P_range.json").read_text()) == {
        "black_m": 500.0, "min_height_m": 540.0, "max_height_m": 2300.0}
    with pytest.raises(RuntimeError, match="no output folder"):
        files.png(Image(np.zeros((2, 2), dtype=np.uint8)), files.PngParams(name="x.png"), CONTEXT)
    files.png(Image(np.zeros((2, 2), dtype=np.uint8)), files.PngParams(name="_empty.png", skip_empty=True), context)
    assert not (tmp_path / "out" / "P_empty.png").exists()


def test_built_in_graphs_are_valid() -> None:
    library = FolderLibrary([GRAPHS_DIR])
    compiler = GraphCompiler(NodeCatalog.from_modules(BUILTIN_MODULES), library)

    maps = 0
    for graph_id in library.ids():
        document = library.get(graph_id)
        assert document is not None
        if not document.inputs:        # maps (with a climate preset) and tools with their own parameter set
            configs = Path(__file__).resolve().parents[2] / "configs"
            own = configs / f"{graph_id}.toml"
            preset = ParameterSet.load(own if own.is_file() else configs / "presets" / "alpine_realistic.toml")
            parameters = ParameterSet.layered([preset], graph_id)
            parameters = ParameterSet(parameters.graph, parameters.params, parameters.nodes, "test",
                                      {**parameters.defaults, "map_name": "Test"})
            compiler.compile(document, parameters)
            maps += 1
    assert maps >= 2


def test_road_network_only_in_the_experimental_map_graphs() -> None:
    library = FolderLibrary([GRAPHS_DIR])
    composer = GraphComposer(library)

    def types(graph_id: str) -> set[str]:          # node types of the graph and all its subgraphs
        document = library.get(graph_id)
        assert document is not None
        nodes = composer.resolve(document).nodes.values()
        return {t for n in nodes for t in (types(n.subgraph) if n.subgraph else {n.type})}

    assert not types("map") & {"roads.network", "mod.roads"}
    assert {"roads.network", "mod.roads"} <= types("map_roads")
    assert not types("map_parcels") & {"roads.network", "mod.roads"}


def test_project_parameter_set_extends_the_preset() -> None:
    project = ParameterSet.load(Path(__file__).resolve().parents[2] / "configs" / "projects" / "golden_test.toml")

    assert project.graph == "map"
    assert project.params["scale"] == 2
    assert project.defaults["biome_limits_m"] == [1000, 1500, 1800]
    assert project.defaults["crops"]["osm_tags"]["maize"] == "corn"
    assert project.given_for(["forest_classes", "scale"]) == {**project.params, "forest_classes": [10]}


def test_lowland_preset_sets_waterways_ramps_and_forest_shrink() -> None:
    configs = Path(__file__).resolve().parents[2] / "configs"
    library = FolderLibrary([GRAPHS_DIR])
    compiler = GraphCompiler(NodeCatalog.from_modules(BUILTIN_MODULES), library)
    project = ParameterSet.load(configs / "projects" / "golden_test.toml")
    lowland = ParameterSet.load(configs / "presets" / "lowland_realistic.toml")
    document = library.get("map")
    assert document is not None

    parameters = ParameterSet(project.graph, project.params, project.nodes, project.source, lowland.defaults)
    plan = compiler.compile(document, ParameterSet.layered([parameters], "map"))

    def params(path: str) -> dict[str, object]:
        return plan.node(path).params.model_dump()

    assert params("terraforming/rivers_and_water/rivers")["real_water_rivers"] == "navigable"
    assert params("final_terrain/ramp")["length_game_m"] == 500
    assert params("terraforming/draft_terrain/draft_ramp")["length_game_m"] == 500
    assert params("biome_creation/forest_edge")["distance_px"] == 2
    assert lowland.defaults["climate"]["name"] == "Lowland"
    assert lowland.defaults["climate_texts"]["strings_de"]["Lowlands"] == "Flachland"


def test_forest_shrinks_along_its_edge_when_set() -> None:
    library, store = FolderLibrary([GRAPHS_DIR]), Path(__file__).resolve().parents[2] / "configs" / "projects"
    compiler = GraphCompiler(NodeCatalog.from_modules(BUILTIN_MODULES), library)
    project = ParameterSet.load(store / "golden_test.toml")
    document = library.get("map")
    assert document is not None

    def distance(params: dict[str, object]) -> object:
        parameters = ParameterSet(project.graph, {**project.params, **params}, project.nodes, project.source,
                                  project.defaults)
        plan = compiler.compile(document, ParameterSet.layered([parameters], "map"))
        return plan.node("biome_creation/forest_edge").params.model_dump()["distance_px"]

    assert distance({}) == 0
    assert distance({"forest_shrink_px": 2}) == 2
    forest = Mask(np.zeros((9, 9), dtype=bool), FRAME).like(np.pad(np.ones((5, 5), dtype=bool), 2))
    edge = masks.near(masks.invert(forest), masks.NearParams(distance_px=1))
    assert masks.subtract(forest, (edge,)).values.sum() == 9                # 5 x 5 -> 3 x 3
    unchanged = masks.near(masks.invert(forest), masks.NearParams(distance_px=0))
    assert np.array_equal(masks.subtract(forest, (unchanged,)).values, forest.values)
