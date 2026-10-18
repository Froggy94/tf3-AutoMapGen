"""Node declarations: signatures become ports, parameters and outputs; mistakes are found when the catalog is built."""

from __future__ import annotations

import types

import pytest

from automapgen.graph import NodeParams, node
from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.errors import NodeDefinitionError
from automapgen.graph.node import Arity
from tests.toy import nodes
from tests.toy.nodes import Numbers


def test_signature_gives_ports_parameters_and_outputs(catalog: NodeCatalog) -> None:
    offset, total, split = (catalog.spec(t) for t in ("toy.offset", "toy.sum", "toy.split"))
    assert offset
    assert total
    assert split

    assert {n: p.arity for n, p in offset.inputs.items()} == {"numbers": Arity.ONE, "shift": Arity.OPTIONAL}
    assert total.inputs["numbers"].arity is Arity.MANY
    assert total.outputs["total"].type.name == "number"
    assert total.context_arg == "context"
    assert total.params_arg is None
    assert list(split.outputs) == ["first", "second"]
    assert offset.description == "Adds `shift` if linked."


def test_parameter_schema_carries_units_and_descriptions(catalog: NodeCatalog) -> None:
    spec = catalog.spec("toy.constant")
    assert spec is not None

    schema = spec.params_model.model_json_schema()

    assert schema["properties"]["value"] == {"default": 1.0, "description": "The value of every element.",
                                             "title": "Value", "type": "number", "unit": "m"}


def test_catalog_lists_data_types_and_nodes(catalog: NodeCatalog) -> None:
    assert catalog.data_types.names() == ["flag", "integer", "number", "numbers", "text"]
    assert "toy.write" in [s.type_id for s in catalog.specs()]


def _module(*members: object) -> types.ModuleType:
    module = types.ModuleType("plugin")
    for i, member in enumerate(members):
        setattr(module, f"m{i}", member)
    return module


def test_unknown_annotation_is_a_definition_error() -> None:
    @node("bad.input")
    def bad(value: list[int]) -> Numbers:
        return Numbers(())

    with pytest.raises(NodeDefinitionError, match=r"node 'bad.input'.*'value'.*no known data type"):
        NodeCatalog.from_modules([nodes, _module(bad)])


def test_return_annotation_is_required() -> None:
    @node("bad.unannotated")
    def f(numbers: Numbers):  # type: ignore[no-untyped-def]
        return numbers

    with pytest.raises(NodeDefinitionError, match="needs a return annotation"):
        NodeCatalog.from_modules([nodes, _module(f)])


def test_node_without_outputs_needs_side_effects() -> None:
    @node("bad.sink")
    def sink(numbers: Numbers) -> None:
        pass

    with pytest.raises(NodeDefinitionError, match="side_effects=True"):
        NodeCatalog.from_modules([nodes, _module(sink)])


@pytest.mark.parametrize("type_id", ["nodot", "Upper.case", "toy."])
def test_type_id_must_be_category_and_name(type_id: str) -> None:
    @node(type_id)
    def f(numbers: Numbers) -> Numbers:
        return numbers

    with pytest.raises(NodeDefinitionError, match=r"category\.name"):
        NodeCatalog.from_modules([nodes, _module(f)])


def test_optional_input_needs_default_none() -> None:
    @node("bad.optional")
    def f(numbers: Numbers | None) -> Numbers:
        return Numbers(())

    with pytest.raises(NodeDefinitionError, match="default None"):
        NodeCatalog.from_modules([nodes, _module(f)])


def test_node_type_declared_twice() -> None:
    @node("toy.constant")
    def again(params: NodeParams) -> Numbers:
        return Numbers(())

    with pytest.raises(NodeDefinitionError, match="declared twice"):
        NodeCatalog.from_modules([nodes, _module(again)])


def test_member_imported_by_two_modules_counts_once() -> None:
    catalog = NodeCatalog.from_modules([nodes, _module(nodes.add, nodes.Numbers)])
    assert catalog.spec("toy.add") is not None
