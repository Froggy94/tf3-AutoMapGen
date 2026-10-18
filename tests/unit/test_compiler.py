"""Compiling graphs: wiring, subgraphs, parameters, versions - and every kind of problem, all reported together."""

from __future__ import annotations

import pytest

from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.errors import GraphError
from automapgen.graph.paramsets import ParameterSet
from automapgen.graph.plan import PortRef
from tests.helpers import compiler, graph

SUM = graph(
    "sum", inputs={"extra": "numbers"}, outputs={"total": "number"},
    params={"value": {"to": ["a.value", "b.value"], "description": "value of both lists"}},
    nodes={"sum": {"type": "toy.sum"}, "b": {"type": "toy.constant"}, "a": {"type": "toy.constant"}},
    links=[["a.numbers", "sum.numbers"], ["b.numbers", "sum.numbers"], ["inputs.extra", "sum.numbers"],
           ["sum.total", "outputs.total"]])


def test_plan_is_sorted_and_wired(catalog: NodeCatalog) -> None:
    plan = compiler(catalog).compile(SUM)

    assert [n.id for n in plan.nodes] == ["b", "a", "sum"]
    assert plan.node("sum").inputs["numbers"] == (PortRef("a", "numbers"), PortRef("b", "numbers"),
                                                  PortRef("inputs", "extra"))
    assert plan.outputs == {"total": PortRef("sum", "total")}
    assert plan.inputs["extra"].name == "numbers"


def test_exposed_parameter_value_comes_from_the_parameter_set(catalog: NodeCatalog) -> None:
    doc = graph("sum", **{**SUM.model_dump(exclude={"id"}), "params": {
        "value": {"to": ["a.value", "b.value"], "value": 4}}})
    sets = ParameterSet(params={"value": 7}, nodes={"b": {"count": 2}})

    without, with_set = compiler(catalog).compile(doc), compiler(catalog).compile(doc, sets)

    assert without.node("a").params.model_dump() == {"value": 4.0, "count": 3}
    assert with_set.node("a").params.model_dump() == {"value": 7.0, "count": 3}
    assert with_set.node("b").params.model_dump() == {"value": 7.0, "count": 2}


def test_subgraph_is_dissolved_into_node_paths(catalog: NodeCatalog) -> None:
    outer = graph(
        "outer", outputs={"result": "number"}, params={"v": {"to": ["inner.value"], "value": 3}},
        nodes={"x": {"type": "toy.constant"}, "inner": {"type": "graph:sum"}},
        links=[["x.numbers", "inner.extra"], ["inner.total", "outputs.result"]])

    plan = compiler(catalog, [SUM]).compile(outer, ParameterSet(nodes={"inner/a": {"count": 1}}))

    assert [n.id for n in plan.nodes] == ["x", "inner/b", "inner/a", "inner/sum"]
    assert plan.node("inner/sum").inputs["numbers"][2] == PortRef("x", "numbers")
    assert plan.outputs["result"] == PortRef("inner/sum", "total")
    assert plan.node("inner/a").params.model_dump() == {"value": 3.0, "count": 1}


BOXES = graph("boxes", outputs={"n": "numbers"}, params={"size": {"to": ["shape.box"], "description": "the size"}},
              nodes={"shape": {"type": "toy.box", "params": {"box": {"width": 5, "height": 6}}}},
              links=[["shape.numbers", "outputs.n"]])
TWO_BOXES = graph("two", outputs={"a": "numbers", "b": "numbers"}, params={"value": {"to": ["c.value"]}},
                  nodes={"c": {"type": "toy.constant"}, "one": {"type": "graph:boxes"},
                         "two": {"type": "graph:boxes"}},
                  links=[["one.n", "outputs.a"], ["two.n", "outputs.b"]])


def test_tables_are_merged_key_by_key_at_every_layer(catalog: NodeCatalog) -> None:
    sets = ParameterSet(params={"value": 2}, nodes={"one/shape": {"box": {"height": 9}}, "two": {"size": {"width": 1}}})

    plan = compiler(catalog, [BOXES]).compile(TWO_BOXES, sets)

    assert plan.node("one/shape").params.model_dump()["box"] == {"width": 5.0, "height": 9.0}
    assert plan.node("two/shape").params.model_dump()["box"] == {"width": 1.0, "height": 6.0}


def test_parameter_slots_name_where_a_value_is_set(catalog: NodeCatalog) -> None:
    plan = compiler(catalog, [BOXES]).compile(TWO_BOXES)

    slots = {(s.owner, s.name): (s.scope, s.local, s.description, [f"{t.node}.{t.name}" for t in s.targets])
             for s in plan.slots}

    assert slots == {("", "value"): ("two", "", "", ["c.value"]),
                     ("c", "count"): ("two", "c", "", ["c.count"]),
                     ("one", "size"): ("two", "one", "the size", ["one/shape.box"]),
                     ("one/shape", "label"): ("boxes", "shape", "", ["one/shape.label"]),
                     ("two", "size"): ("two", "two", "the size", ["two/shape.box"]),
                     ("two/shape", "label"): ("boxes", "shape", "", ["two/shape.label"])}


def test_node_parameters_of_old_versions_are_migrated(catalog: NodeCatalog) -> None:
    doc = graph(outputs={"n": "numbers"},
                nodes={"c": {"type": "toy.constant"}, "s": {"type": "toy.scale", "version": 1, "params": {"times": 5}}},
                links=[["c.numbers", "s.numbers"], ["s.numbers", "outputs.n"]])

    assert compiler(catalog).compile(doc).node("s").params.model_dump() == {"factor": 5.0}


def problems(catalog: NodeCatalog, doc: object, *, library: tuple[object, ...] = (),
             parameters: ParameterSet | None = None) -> list[str]:
    with pytest.raises(GraphError) as error:
        compiler(catalog, library).compile(doc, parameters)  # type: ignore[arg-type]
    return [str(p) for p in error.value.problems]


def test_all_problems_are_reported_together(catalog: NodeCatalog) -> None:
    doc = graph(
        inputs={"src": "nope"}, outputs={"total": "number", "unused": "number"},
        params={"p": {"to": ["ghost.value", "a.colour"]}},
        nodes={"a": {"type": "toy.constant", "params": {"count": 0}}, "w": {"type": "toy.warp"},
               "add": {"type": "toy.add"}, "s": {"type": "toy.scale", "version": 9}, "Bad": {"type": "toy.sum"}},
        links=[["a.numbers", "add.a"], ["a.numbers", "add.a"], ["a.total", "outputs.total"], ["a", "add.b"],
               ["s.numbers", "outputs.total"], ["inputs.zz", "s.numbers"], ["outputs.total", "s.numbers"]])

    found = problems(catalog, doc)

    expected = [
        "input 'src' has unknown data type 'nope'",
        "node id 'Bad' must be lower case",
        "node 'w': unknown node type 'toy.warp'",
        "parameter 'p' sets 'ghost.value' of unknown node 'ghost'",
        "parameter 'p' sets unknown parameter 'a.colour'",
        "node 's': written for version 9 of 'toy.scale', known is 2",
        "parameter 'count': Input should be greater than or equal to 1",
        "'a' has no output 'total' (has: numbers)",
        "'a' must look like 'node.port'",
        "graph has no input 'zz'",
        "'outputs' cannot be used on this side of a link",
        "input 'a' takes one link, has 2",
        "input 'b' is not connected",
        "input 'numbers' is not connected",
        "output 'unused' needs exactly one link, has 0",
    ]
    for text in expected:
        assert any(text in f for f in found), (text, found)


def test_type_mismatch_and_cycle(catalog: NodeCatalog) -> None:
    doc = graph(
        outputs={"n": "numbers"},
        nodes={"c": {"type": "toy.constant"}, "s": {"type": "toy.sum"}, "o": {"type": "toy.offset"},
               "p": {"type": "toy.offset"}},
        links=[["s.total", "o.numbers"], ["c.numbers", "s.numbers"], ["p.numbers", "o.shift"],
               ["o.numbers", "p.numbers"], ["o.numbers", "outputs.n"]])

    found = problems(catalog, doc)

    assert any("link s.total -> o.numbers: 'number' does not fit 'numbers'" in f for f in found)
    assert any("links form a cycle through: o, p" in f for f in found)


def test_subgraph_problems(catalog: NodeCatalog) -> None:
    selfish = graph("selfish", nodes={"me": {"type": "graph:selfish"}})
    doc = graph(nodes={"s": {"type": "graph:sum", "params": {"nope": 1}}, "m": {"type": "graph:missing"},
                       "x": {"type": "graph:selfish"}},
                links=[["s.total", "s.wrong"]])

    found = problems(catalog, doc, library=(SUM, selfish))

    assert any("node 's': graph 'sum' has no parameter 'nope'" in f for f in found)
    assert any("unknown graph 'missing'" in f for f in found)
    assert any("graph uses itself: g -> selfish -> selfish" in f for f in found)
    assert any("subgraph 's' has no input 'wrong'" in f for f in found)
    assert any("node 's': input 'extra' is not connected" in f for f in found)


def test_parameter_set_with_unknown_names(catalog: NodeCatalog) -> None:
    found = problems(catalog, SUM, parameters=ParameterSet(params={"zz": 1}, nodes={"ghost": {}}, source="set.toml"))

    assert "set.toml: graph 'sum' has no parameter 'zz'" in found
    assert "set.toml: no node 'ghost' in graph 'sum'" in found
