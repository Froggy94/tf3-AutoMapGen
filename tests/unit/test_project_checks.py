"""Checks beyond the compiler (game material names, the climate name of a changed look) and grouped problems."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from automapgen.app.project_checks import MATERIALS_FOLDER, ProjectChecks
from automapgen.app.projects import ProjectStore
from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.compiler import GraphCompiler
from automapgen.graph.errors import GraphError
from automapgen.graph.library import FolderLibrary
from automapgen.graph.paramsets import ParameterSet, merged_tables
from automapgen.graph.plan import ExecutionPlan
from automapgen.graphs import GRAPHS_DIR
from automapgen.nodes import BUILTIN_MODULES

CONFIGS = Path(__file__).resolve().parents[2] / "configs"
LIBRARY = FolderLibrary([GRAPHS_DIR])


def _plan(changes: Mapping[str, Any]) -> ExecutionPlan:
    project = ProjectStore(CONFIGS / "projects").merged("innsbruck_example")
    parameters = ParameterSet(project.graph, project.params, project.nodes, project.source,
                              merged_tables(project.defaults, changes))
    return GraphCompiler(NodeCatalog.discover(BUILTIN_MODULES), LIBRARY).compile(LIBRARY.get("map"),  # type: ignore[arg-type]
                                                                                 parameters)


def _game(tmp_path: Path, materials: list[str]) -> Path:
    folder = tmp_path / "base" / "content" / MATERIALS_FOLDER
    folder.mkdir(parents=True)
    for name in [*materials, "shared"]:
        (folder / f"{name}.zip").write_bytes(b"")
    return tmp_path


def test_unknown_game_materials_are_problems_when_the_game_folder_is_known(tmp_path: Path) -> None:
    brown = {"climate": {"surfaces": {"alpine_grass": {"look": {"base": {"kind": "game", "name": "grass_braun"}}}}}}
    plan = _plan(brown)
    known = ["corn", "gr_br_sub", "grass_alpine", "grass_brown", "grass_dark_green", "grass_green",
             "grass_light_green", "gravel_01", "rock", "scree", "soil_01", "water_dirty", "wheat"]

    problems = ProjectChecks(lambda: _game(tmp_path, known)).problems(plan)

    assert [p.message.split(" (")[0] for p in problems] == ["unknown game material 'grass_braun'"]
    assert ProjectChecks(lambda: None).problems(plan) == []          # without the game folder: not checked


def test_a_changed_look_without_an_own_name_is_a_warning() -> None:
    changed = {"defaults": {"climate": {"heights": {"tree_line": 1900}}}}
    preset: dict[str, Any] = {"climate": {}}
    landscape: dict[str, Any] = {"climate": {}, "transition": {"name": "Alpine North-South"}}

    assert "[defaults.climate] name" in ProjectChecks.warnings(changed, preset)[0]
    assert "[defaults.transition] name" in ProjectChecks.warnings(changed, landscape)[0]   # the transition names it
    assert ProjectChecks.warnings({"defaults": {"climate": {"name": "Alpine Brown", "heights": {}}}}, preset) == []
    assert ProjectChecks.warnings({"defaults": {"climate": {"name": "Alpine Brown"}}}, preset) == []
    assert ProjectChecks.warnings({"params": {"map_name": "X"}}, preset) == []


def test_the_same_parameter_problem_of_several_nodes_is_one_problem() -> None:
    with pytest.raises(GraphError) as error:
        _plan({"climate": {"heights": {"tree_lin": 1900}}})

    typo = [p for p in error.value.problems if "tree_lin" in p.where]
    assert len(typo) == 1
    assert "nodes 'originals', 'identity'" in typo[0].where
