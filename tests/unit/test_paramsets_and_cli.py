"""Parameter set files and layers; the command line."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from automapgen.cli import Cli
from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.document import GraphFile
from automapgen.graph.errors import GraphError
from automapgen.graph.paramsets import ParameterSet
from automapgen.graph.render import MermaidDiagram
from tests.helpers import graph

DOC = graph("demo", outputs={"total": "number"}, params={"value": {"to": ["a.value"]}},
            nodes={"a": {"type": "toy.constant"}, "end": {"type": "toy.sum"}},
            links=[["a.numbers", "end.numbers"], ["end.total", "outputs.total"]])


def test_parameter_set_file_and_layers(tmp_path: Path) -> None:
    preset = tmp_path / "preset.toml"
    preset.write_text('graph = "demo"\n[params]\nvalue = 2\n[nodes.a]\ncount = 4\n', encoding="utf-8")
    project = ParameterSet(params={"value": 5}, nodes={"a": {"value": 1}}, source="project")

    merged = ParameterSet.layered([ParameterSet.load(preset), project], "demo")

    assert merged.params == {"value": 5}
    assert merged.nodes == {"a": {"count": 4, "value": 1}}
    assert merged.source == "preset.toml + project"
    with pytest.raises(GraphError, match="made for graph 'demo', not 'other'"):
        ParameterSet.layered([ParameterSet.load(preset)], "other")


def test_parameter_set_extends_other_sets(tmp_path: Path) -> None:
    (tmp_path / "presets").mkdir()
    (tmp_path / "presets" / "base.toml").write_text('[params]\nvalue = 1\ncount = 2\n[nodes.a]\nx = 1\n',
                                                    encoding="utf-8")
    (tmp_path / "project.toml").write_text('graph = "demo"\nextends = ["presets/base.toml"]\n[params]\nvalue = 5\n'
                                           '[nodes.a]\ny = 2\n', encoding="utf-8")
    (tmp_path / "loop.toml").write_text('extends = ["loop.toml"]\n', encoding="utf-8")
    (tmp_path / "other.toml").write_text('graph = "other"\nextends = ["project.toml"]\n', encoding="utf-8")

    project = ParameterSet.load(tmp_path / "project.toml")

    assert project.graph == "demo"
    assert project.params == {"value": 5, "count": 2}
    assert project.nodes == {"a": {"x": 1, "y": 2}}
    with pytest.raises(GraphError, match="extends itself"):
        ParameterSet.load(tmp_path / "loop.toml")
    assert ParameterSet.load(tmp_path / "other.toml").graph == "other"   # takes over the values for another graph
    with pytest.raises(GraphError, match="not found"):
        ParameterSet.load(tmp_path / "missing.toml")


def test_layers_merge_tables_key_by_key(tmp_path: Path) -> None:
    (tmp_path / "base.toml").write_text('[defaults.climate]\nname = "North"\nlimits = [1, 2]\n'
                                        '[defaults.climate.forest]\ndensity = 0.1\nshare = 0.4\n', encoding="utf-8")
    (tmp_path / "south.toml").write_text('extends = ["base.toml"]\n[defaults.climate]\nname = "South"\nlimits = [3]\n'
                                         '[defaults.climate.forest]\nshare = 0.6\n', encoding="utf-8")

    climate = ParameterSet.load(tmp_path / "south.toml").defaults["climate"]

    assert climate == {"name": "South", "limits": [3], "forest": {"density": 0.1, "share": 0.6}}


def test_parameter_set_given_as_data(tmp_path: Path) -> None:
    (tmp_path / "base.toml").write_text("[defaults]\nvalue = 2\n[params]\nother = 1\n", encoding="utf-8")

    data = ParameterSet.of({"extends": ["base.toml"], "params": {"other": 3}}, tmp_path, "page")

    assert (data.defaults, data.params, data.source) == ({"value": 2}, {"other": 3}, "page")
    with pytest.raises(GraphError, match="unknown key 'what'"):
        ParameterSet.of({"what": 1}, tmp_path)


def test_defaults_reach_only_graphs_that_expose_them(tmp_path: Path) -> None:
    (tmp_path / "preset.toml").write_text("[defaults]\nvalue = 2\nunused_elsewhere = 1\n", encoding="utf-8")
    preset = ParameterSet.load(tmp_path / "preset.toml")

    assert preset.given_for(["value"]) == {"value": 2}
    assert ParameterSet(params={"value": 5}, defaults={"value": 2}).given_for(["value"]) == {"value": 5}


def test_parameter_set_file_problems(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("colour = 1\n[nodes]\na = 3\n", encoding="utf-8")

    with pytest.raises(GraphError, match=r"unknown key 'colour'(.|\n)*nodes.a must be a table"):
        ParameterSet.load(bad)


def test_mermaid_diagram() -> None:
    text = MermaidDiagram().render(DOC)

    assert text.startswith("flowchart LR\n")
    assert 'n_end["end<br/><small>toy.sum</small>"]' in text
    assert 'n_a -- "numbers" --> n_end' in text                     # same port name on both ends: once


def test_mermaid_diagram_shows_subgraph_titles_and_one_arrow_per_pair() -> None:
    doc = graph("outer", nodes={"inner": {"type": "graph:inner_graph"}, "plain": {"type": "graph:untitled"}},
                links=[["inner.a", "plain.a"], ["inner.b", "plain.c"]])

    text = MermaidDiagram(lambda graph_id: {"inner_graph": "Inner Graph"}.get(graph_id, "")).render(doc)

    assert 'n_inner[["<b>Inner Graph</b><br/><small>inner</small>"]]' in text
    assert 'n_plain[["plain<br/><small>graph:untitled</small>"]]' in text
    assert 'n_inner -- "a<br/>b -> c" --> n_plain' in text


def cli(catalog: NodeCatalog, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = Cli(catalog, out, err).main(argv)
    return code, out.getvalue(), err.getvalue()


def test_cli_check_show_nodes_schema(catalog: NodeCatalog, tmp_path: Path) -> None:
    GraphFile.save(DOC, tmp_path / "demo.graph.json")
    (tmp_path / "set.toml").write_text("[params]\nvalue = 3\n", encoding="utf-8")
    (tmp_path / "bad.toml").write_text("[params]\nzz = 3\n", encoding="utf-8")

    assert cli(catalog, "check", str(tmp_path / "demo.graph.json"), "--set", str(tmp_path / "set.toml"))[1] == \
        "graph 'demo' is valid: 2 nodes, inputs -, outputs total\n"
    code, _, err = cli(catalog, "check", "demo", "--graphs", str(tmp_path), "--set", str(tmp_path / "bad.toml"))
    assert code == 1
    assert "bad.toml: graph 'demo' has no parameter 'zz'" in err
    assert cli(catalog, "show", "demo", "--graphs", str(tmp_path))[1].startswith("flowchart LR")
    assert "toy.scale v2  (numbers: numbers) -> (numbers: numbers)  params: factor" in cli(catalog, "nodes")[1]
    assert '"$schema"' in cli(catalog, "schema")[1]
    assert cli(catalog, "check", "nothing", "--graphs", str(tmp_path))[0] == 1
    run = cli(catalog, "run", "demo", "--graphs", str(tmp_path), "--output", str(tmp_path / "out"))
    assert run[1] == "done: 2 nodes ran, 0 reused\n"


def test_cli_finds_own_graphs_in_the_configs(catalog: NodeCatalog, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "configs" / "graphs").mkdir(parents=True)
    GraphFile.save(DOC, tmp_path / "configs" / "graphs" / "demo.graph.json")
    monkeypatch.chdir(tmp_path)

    assert cli(catalog, "show", "demo")[1].startswith("flowchart LR")


def test_cli_projects(catalog: NodeCatalog, tmp_path: Path) -> None:
    GraphFile.save(DOC, tmp_path / "demo.graph.json")
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "alps.toml").write_text('graph = "demo"\n[params]\nvalue = 3\n', encoding="utf-8")

    def run(*argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        return Cli(catalog, out, err, projects).main(argv), out.getvalue(), err.getvalue()

    assert run("projects")[1] == "alps  graph demo  map -\n"
    assert run("check", "--project", "alps", "--graphs", str(tmp_path))[1].startswith("graph 'demo' is valid")
    assert "parameter set not found" in run("check", "--project", "nope", "--graphs", str(tmp_path))[2]
    assert "no graph given" in run("check", "--graphs", str(tmp_path))[2]


def test_json_documents_are_plain_data() -> None:
    """A document behaves the same fresh and after the cache: NumPy scalars become Python numbers, tuples lists."""
    import numpy as np

    from automapgen.nodes.documents import JsonDocument

    document = JsonDocument({"x": np.float64(1.5), "pts": (1, 2), "n": np.int32(3)})

    assert document.data == {"x": 1.5, "pts": [1, 2], "n": 3}
    assert type(document.data["x"]) is float
