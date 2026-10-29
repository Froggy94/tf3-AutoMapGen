"""The application: map sizes, project files, local settings, the GUI's API (without a run)."""

from __future__ import annotations

import json
import shutil
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from automapgen.app.gui.advanced import AdvancedSettings
from automapgen.app.gui.api import GuiApi
from automapgen.app.gui.graph_view import GraphView
from automapgen.app.gui.run import GenerationRun, MapJob
from automapgen.app.gui.server import LocalHttpServer, PortInUseError
from automapgen.app.gui.settings import GuiPaths, GuiSettings
from automapgen.app.gui.summary import MIN_HEIGHT_RAISE_GAME_M, ImportSummary
from automapgen.app.local_settings import LocalSettings
from automapgen.app.map_sizes import MapSizeCatalog
from automapgen.app.projects import ProjectStore
from automapgen.app.toml_writer import TomlWriter
from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.library import FolderLibrary
from automapgen.graph.paramsets import ParameterSet
from automapgen.graphs import GRAPHS_DIR
from automapgen.nodes import BUILTIN_MODULES

CONFIGS = Path(__file__).resolve().parents[2] / "configs"


def test_map_sizes() -> None:
    sizes = MapSizeCatalog.load(CONFIGS / "map_sizes.toml")

    assert sizes.game_km("very_large", "1x2") == (11.264, 22.528)
    assert sizes.find(11.264, 22.528) == ("very_large", "1x2")
    assert sizes.find(10.0, 10.0) is None
    with pytest.raises(ValueError, match="unknown map size"):
        sizes.game_km("enormous", "1x2")


def test_toml_writer_puts_values_before_tables() -> None:
    text = TomlWriter().write({"graph": "map", "params": {"crop": {"west": 1.5, "south": 2}, "names": ["a"]},
                               "nodes": {"draft/water": {"banks": {"smooth_factor": 5}}}}, "Header")

    assert text == ('# Header\ngraph = "map"\n\n[params]\ncrop = { west = 1.5, south = 2 }\nnames = ["a"]\n\n'
                    '[nodes."draft/water"]\nbanks = { smooth_factor = 5 }\n')


def test_projects_round_trip_and_names(tmp_path: Path) -> None:
    shutil.copytree(CONFIGS, tmp_path / "configs")
    store = ProjectStore(tmp_path / "configs" / "projects")
    before = store.merged("brenner")

    store.save("brenner", store.load("brenner"))
    after = store.merged("brenner")

    assert (after.params, after.defaults, after.nodes) == (before.params, before.defaults, before.nodes)
    assert store.new_id("Tirol_Nord") == "tirol_nord"
    assert any(e.id == "brenner" and e.map_name == "Brenner" for e in store.entries())
    assert {look.path for look in store.looks(tmp_path / "configs")} >= {
        "../presets/alpine_realistic.toml", "../landscapes/brenner_north_south.toml"}
    with pytest.raises(ValueError, match="invalid project name"):
        store.path("../evil")
    with pytest.raises(Exception, match="nope"):
        store.save("broken", {"extends": ["nope.toml"]})
    assert not store.path("broken").exists()


def test_local_settings_keep_other_values_when_a_secret_is_saved(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('game_folder = "G:/TF3"\n', encoding="utf-8")

    LocalSettings.save_secret(path, "opentopography_api_key", "abc")
    saved = LocalSettings.load(path)
    LocalSettings.save_secret(path, "opentopography_api_key", "")

    assert saved == LocalSettings(Path("G:/TF3"), None, {"opentopography_api_key": "abc"})
    assert LocalSettings.load(path) == LocalSettings(Path("G:/TF3"))


def test_local_settings_keep_secrets_when_the_folders_are_saved(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    LocalSettings.save_secret(path, "opentopography_api_key", "abc")

    LocalSettings.save_folders(path, Path("G:/TF3"), None)

    assert LocalSettings.load(path) == LocalSettings(Path("G:/TF3"), None, {"opentopography_api_key": "abc"})


def test_gui_api_setup_project_save(tmp_path: Path) -> None:
    shutil.copytree(CONFIGS, tmp_path / "configs")
    paths = GuiPaths(tmp_path)
    catalog = NodeCatalog.discover(BUILTIN_MODULES)
    store = ProjectStore(paths.projects)
    library = FolderLibrary([GRAPHS_DIR])
    settings = GuiSettings.load(paths.configs / "gui.toml")
    run = GenerationRun(MapJob(paths, catalog, library, store, settings.steps), tmp_path)
    api = GuiApi(paths, settings, MapSizeCatalog.load(paths.configs / "map_sizes.toml"), store, run, catalog,
                 AdvancedSettings(settings.advanced, catalog, library, store),
                 GraphView(settings.graphs, catalog, library, store))

    setup = api.get("/api/setup", {}).body
    project = api.get("/api/project", {"id": "brenner"}).body
    doc = {**project["doc"], "params": {**project["doc"]["params"], "map_name": "Brenner_Copy"}}
    saved = api.post("/api/save", {"id": None, "doc": doc})

    assert setup["towns_per_100_km2"] > 0
    assert setup["api_key_set"] is False
    assert [g["id"] for g in setup["graphs"]] == ["map", "map_parcels"]
    assert setup["graphs"][0]["description"].startswith("The complete map")
    assert saved.status == 200
    assert saved.body == {"id": "brenner_copy"}
    assert store.merged("brenner_copy").params["map_name"] == "Brenner_Copy"
    diagram = api.get("/api/graph", {"id": "map_roads"}).body
    assert diagram["mermaid"].startswith("flowchart LR")
    assert diagram["subgraphs"]["n_climate"] == "map_climate"
    assert '<b>Climate Mod</b><br/><small>climate</small>' in diagram["mermaid"]
    assert diagram["subgraphs"]["n_final_terrain"] == "map_final_terrain_roads"   # what map_roads changes
    assert api.get("/api/graph", {"id": "nope"}).body == {"error": "unknown graph 'nope'"}
    assert api.post("/api/check", {"doc": project["doc"]}).body == {"problems": [], "warnings": []}
    wrong = {**project["doc"], "graph": "map", "params": {**project["doc"]["params"], "road_min_capacity": 5}}
    assert "no parameter 'road_min_capacity'" in api.post("/api/check", {"doc": wrong}).body["problems"][0]
    assert api.get("/api/nothing", {}).status == 404
    assert api.post("/api/run", {"id": "x", "doc": {"unknown": 1}}).status == 400
    assert api.get("/output/../configs/gui.toml.png", {}).status == 404
    assert json.loads(api.get("/api/log", {"from": "0"}).payload())["running"] is False
    (tmp_path / "game" / "base" / "content").mkdir(parents=True)
    folders = api.post("/api/folders", {"game_folder": str(tmp_path / "game"), "user_data_folder": ""})
    assert folders.body == {"game_folder": str(tmp_path / "game"), "user_data": ""}
    assert api.get("/api/setup", {}).body["game_folder"] == str(tmp_path / "game")
    assert "no base/content" in api.post("/api/folders", {"game_folder": str(tmp_path)}).body["error"]


def test_a_second_gui_on_the_same_port_is_refused() -> None:
    first = LocalHttpServer.at(0, BaseHTTPRequestHandler)
    try:
        with pytest.raises(PortInUseError, match="already running"):
            LocalHttpServer.at(first.server_address[1], BaseHTTPRequestHandler)
    finally:
        first.server_close()


def test_import_summary_raises_the_minimum_height_so_the_water_is_not_too_high(tmp_path: Path) -> None:
    folder = tmp_path / "Map"
    folder.mkdir()
    (folder / "Map_height_range.json").write_text(
        '{"black_m": 509.2, "min_height_m": 549.2, "max_height_m": 2787.0}', encoding="utf-8")

    summary = ImportSummary(tmp_path).build(ParameterSet(params={"map_name": "Map", "scale": 2}))

    assert MIN_HEIGHT_RAISE_GAME_M == 3
    assert summary["min_game"] == pytest.approx(-17)
    assert summary["max_game"] == pytest.approx((2787.0 - 549.2) / 2)

    real = ImportSummary(tmp_path).build(ParameterSet(params={"map_name": "Map", "scale": 2, "height_scale": 1}))
    assert real["min_game"] == pytest.approx(509.2 - 549.2 + 3)
    assert real["max_game"] == pytest.approx(2787.0 - 549.2)
