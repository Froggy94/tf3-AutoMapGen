"""Graph documents: files, schema, libraries and composition in layers (extends)."""

from __future__ import annotations

from pathlib import Path

import pytest

from automapgen.graph.composer import GraphComposer
from automapgen.graph.document import GraphDocument, GraphFile
from automapgen.graph.errors import GraphError
from automapgen.graph.library import FolderLibrary, MemoryLibrary
from tests.helpers import graph

BASE = graph(
    "base", outputs={"total": "number"},
    nodes={"a": {"type": "toy.constant"}, "b": {"type": "toy.constant", "params": {"value": 2}},
           "sum": {"type": "toy.sum"}},
    links=[["a.numbers", "sum.numbers"], ["b.numbers", "sum.numbers"], ["sum.total", "outputs.total"]],
    ui={"a": {"x": 1}})


def test_file_round_trip_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "base.graph.json"
    GraphFile.save(BASE, path)

    loaded = GraphFile.load(path)

    assert loaded == BASE
    assert GraphFile.dumps(loaded) == path.read_text(encoding="utf-8")
    assert '"format"' not in path.read_text(encoding="utf-8")   # defaults are not written


def test_schema_reference_and_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "x.graph.json"
    path.write_text('{"$schema": "graph.schema.json", "id": "x", "nodez": {}}', encoding="utf-8")

    with pytest.raises(GraphError, match="nodez"):
        GraphFile.load(path)
    assert GraphDocument.model_validate({"$schema": "s.json", "id": "x"}).schema_ref == "s.json"
    assert "$schema" in GraphFile.json_schema()["properties"]   # type: ignore[operator]


def test_remove_and_unlink_need_extends() -> None:
    with pytest.raises(ValueError, match="only allowed together with 'extends'"):
        graph(remove=["a"])


def test_folder_library_finds_first_and_checks_ids(tmp_path: Path) -> None:
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    GraphFile.save(BASE, tmp_path / "one" / "base.graph.json")
    GraphFile.save(graph("other"), tmp_path / "two" / "base.graph.json")
    GraphFile.save(graph("x"), tmp_path / "two" / "x.graph.json")

    library = FolderLibrary([tmp_path / "one", tmp_path / "two"])

    assert library.get("base") == BASE
    assert library.get("missing") is None
    assert library.ids() == ["base", "x"]
    with pytest.raises(GraphError, match="document id is 'other'"):
        FolderLibrary([tmp_path / "two"]).get("base")


def test_extends_merges_differences() -> None:
    child = graph("child", extends="base",
                  nodes={"b": {"type": "toy.constant", "params": {"value": 5}}, "c": {"type": "toy.constant"}},
                  links=[["c.numbers", "sum.numbers"]], ui={"c": {"x": 3}})

    resolved = GraphComposer(MemoryLibrary([BASE])).resolve(child)

    assert resolved.id == "child"
    assert resolved.extends is None
    assert resolved.nodes["b"].params == {"value": 5}
    assert "c" in resolved.nodes
    assert len(resolved.links) == 4
    assert resolved.ui == {"a": {"x": 1}, "c": {"x": 3}}


def test_extends_removes_nodes_with_their_links_and_unlinks() -> None:
    child = graph("child", extends="base", remove=["b"], unlink=[["a.numbers", "sum.numbers"]])

    resolved = GraphComposer(MemoryLibrary([BASE])).resolve(child)

    assert set(resolved.nodes) == {"a", "sum"}
    assert resolved.links == [("sum.total", "outputs.total")]


def test_extends_reports_unknown_bases_cycles_and_differences() -> None:
    composer = GraphComposer(MemoryLibrary([BASE, graph("loop_a", extends="loop_b"),
                                            graph("loop_b", extends="loop_a")]))

    with pytest.raises(GraphError, match="unknown graph 'nope'"):
        composer.resolve(graph("c", extends="nope"))
    with pytest.raises(GraphError, match="extends itself"):
        composer.resolve(graph("loop_a", extends="loop_b"))
    with pytest.raises(GraphError, match=r"removes unknown node 'zz'(.|\n)*unlinks unknown link"):
        composer.resolve(graph("c", extends="base", remove=["zz"], unlink=[["a.x", "b.y"]]))
