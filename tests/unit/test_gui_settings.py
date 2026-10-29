"""The GUI's advanced settings: form fields from parameter schemas, the description of a project, the API routes."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Any, Literal

import pytest
from pydantic import Field

from automapgen.app.gui.advanced import AdvancedSettings, SettingEntry
from automapgen.app.gui.api import GuiApi
from automapgen.app.gui.forms import FormField, SchemaForms
from automapgen.app.gui.graph_view import GraphView
from automapgen.app.gui.run import GenerationRun, MapJob
from automapgen.app.gui.settings import GuiPaths, GuiSettings
from automapgen.app.map_sizes import MapSizeCatalog
from automapgen.app.projects import ProjectStore
from automapgen.app.steps import StepPlan
from automapgen.graph import NodeParams, param
from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.compiler import GraphCompiler
from automapgen.graph.library import FolderLibrary
from automapgen.graph.paramsets import ParameterSet
from automapgen.graphs import GRAPHS_DIR
from automapgen.nodes import BUILTIN_MODULES

CONFIGS = Path(__file__).resolve().parents[2] / "configs"


class Inner(NodeParams):
    depth: float = param(2.0, unit="m", ge=0)
    """How deep."""


class Round(NodeParams):
    kind: Literal["round"] = "round"
    radius: float = 1.0
    """Radius."""


class Square(NodeParams):
    kind: Literal["square"] = "square"
    side: float = 1.0
    """Side."""


class Example(NodeParams):
    limit: float | None = param(None, unit="m")
    """Upper limit; None = none."""
    mode: Literal["box", "nearest"] = "box"
    """Method."""
    pair: tuple[float, float] = (1.0, 2.0)
    """Two values."""
    names: tuple[str, ...] = ("a",)
    """Names."""
    inner: Inner = Inner()
    """A table."""
    shares: dict[str, float] = Field(default_factory=lambda: {"wheat": 0.5})
    """Free keys."""
    shape: Annotated[Round | Square, Field(discriminator="kind")] = Round()
    """One of two."""
    rows: tuple[Inner, ...] = ()
    """A list of tables."""


def _field(name: str) -> FormField:
    schema = Example.model_json_schema()
    return SchemaForms(schema.get("$defs", {})).field(schema["properties"][name], Example().model_dump()[name])


def test_form_fields_from_schemas() -> None:
    assert _field("limit") == FormField("number", "Upper limit; None = none.", "m", nullable=True)
    assert _field("mode").choices == ("box", "nearest")
    assert (_field("pair").kind, _field("pair").item, _field("pair").length) == ("list", "number", 2)
    assert (_field("names").item, _field("names").length) == ("text", None)
    inner = _field("inner")
    assert (inner.kind, inner.description) == ("object", "A table.")
    assert inner.fields == (("depth", FormField("number", "How deep.", "m", limits={"minimum": 0})),)
    assert [key for key, _ in _field("shares").fields] == ["wheat"]
    assert [key for key, _ in _field("shape").fields] == ["kind", "radius"]
    assert _field("rows").kind == "json"
    assert _field("inner").as_json() == {"kind": "object", "description": "A table.", "fields": [
        {"key": "depth", "kind": "number", "description": "How deep.", "unit": "m", "limits": {"minimum": 0}}]}


@pytest.fixture
def advanced() -> AdvancedSettings:
    settings = GuiSettings.load(CONFIGS / "gui.toml")
    return AdvancedSettings(settings.advanced, NodeCatalog.discover(BUILTIN_MODULES), FolderLibrary([GRAPHS_DIR]),
                            ProjectStore(CONFIGS / "projects"))


def _entries(advanced: AdvancedSettings, doc: dict[str, Any]) -> dict[tuple[str, ...], SettingEntry]:
    return {e.stores[0]: e for s in advanced.describe(doc) for g in s.groups for e in g.entries}


def test_advanced_settings_of_a_project(advanced: AdvancedSettings) -> None:
    doc = {**ProjectStore(CONFIGS / "projects").load("brenner"), "graph": "map"}   # whatever graph the GUI set

    entries = _entries(advanced, doc)

    banks = entries["nodes", "terraforming/draft_terrain/draft/water", "banks"]
    assert banks.stores == (("nodes", "terraforming/draft_terrain/draft/water", "banks"),
                            ("nodes", "final_terrain/final/water", "banks"))
    assert banks.base["smooth_factor"] == 4            # the look's value, not the project's change (5)
    climate = entries["defaults", "climate"]
    assert climate.reads == (("params", "climate"), ("defaults", "climate"))
    assert climate.base["name"] == "Alpine Realistic"
    assert ("params", "road_min_capacity") not in entries         # the road network is not in the graph map
    assert ("params", "crop") not in entries           # the main page's
    assert ("params", "height_scale") not in entries
    assert not any(store[1].endswith("_png") for store in entries if store[0] == "nodes")    # hidden: file.*
    assert [s.key for s in advanced.describe(doc)][:2] == ["terrain", "water"]


def test_changes_at_the_stores_reach_the_nodes(advanced: AdvancedSettings) -> None:
    store = ProjectStore(CONFIGS / "projects")
    doc = {**advanced.base_document(store.load("golden_test")),
           "defaults": {"climate": {"heights": {"tree_line": 2222}}},
           "params": {**store.load("golden_test")["params"], "road_min_capacity": 42},
           "nodes": {"terraforming/draft_terrain/draft/water": {"banks": {"passes": 3}},
                     "final_terrain/final/water": {"banks": {"passes": 3}}}}
    library = FolderLibrary([GRAPHS_DIR])

    plan = GraphCompiler(NodeCatalog.discover(BUILTIN_MODULES), library).compile(library.get("map_roads"),  # type: ignore[arg-type]
                                                                                store.parameters(doc))

    assert plan.node("climate/climate_mod/originals").params.model_dump()["climate"]["heights"]["tree_line"] == 2222
    assert plan.node("final_terrain/road_network").params.model_dump()["min_capacity"] == 42
    banks = plan.node("final_terrain/final/water").params.model_dump()["banks"]
    assert (banks["passes"], banks["smooth_factor"]) == (3, 4)


@pytest.mark.parametrize("project", ["innsbruck_example", "brenner", "golden_test", "golden_test_parcels"])
def test_every_sink_of_the_map_graphs_belongs_to_a_processing_step(project: str) -> None:
    steps = GuiSettings.load(CONFIGS / "gui.toml").steps
    store, library = ProjectStore(CONFIGS / "projects"), FolderLibrary([GRAPHS_DIR])
    parameters = store.merged(project)
    document = library.get(parameters.graph or "map")
    assert document is not None
    plan = GraphCompiler(NodeCatalog.discover(BUILTIN_MODULES), library).compile(
        document, ParameterSet.layered([parameters], document.id))

    step_plan = StepPlan(steps, plan)

    assert step_plan.unassigned == []
    assert step_plan.nodes("heightmap") == ["final_terrain/heightmap_png", "final_terrain/height_range"]
    assert "source_data/dem" in step_plan.nodes("download")
    assert "climate/climate_mod_folder" in step_plan.nodes("climate")
    assert set(step_plan.targets({"heightmap"})) == {"final_terrain/heightmap_png", "final_terrain/height_range"}
    with pytest.raises(ValueError, match="unknown processing steps: nope"):
        step_plan.targets({"nope"})
    with pytest.raises(ValueError, match="at least one"):
        step_plan.targets(set())


def test_gui_api_settings_and_static_files(tmp_path: Path) -> None:
    shutil.copytree(CONFIGS, tmp_path / "configs")
    paths = GuiPaths(tmp_path)
    catalog = NodeCatalog.discover(BUILTIN_MODULES)
    store, library = ProjectStore(paths.projects), FolderLibrary([GRAPHS_DIR])
    settings = GuiSettings.load(paths.configs / "gui.toml")
    api = GuiApi(paths, settings, MapSizeCatalog.load(paths.configs / "map_sizes.toml"), store,
                 GenerationRun(MapJob(paths, catalog, library, store, settings.steps), tmp_path), catalog,
                 AdvancedSettings(settings.advanced, catalog, library, store),
                 GraphView(settings.graphs, catalog, library, store))

    form = api.post("/api/settings", {"doc": store.load("golden_test")})

    assert form.status == 200
    assert "climate" in [s["key"] for s in form.body["sections"]]
    assert api.post("/api/settings", {"doc": {"graph": "nope"}}).status == 400
    assert api.get("/static/advanced.js", {}).content_type.startswith("text/javascript")
    assert "FIELD_KINDS" in api.get("/static/advanced.js", {}).body
    assert api.get("/static/../api.py", {}).status == 404
    assert api.get("/static/forms.py", {}).status == 404
